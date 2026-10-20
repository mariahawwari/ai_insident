# Runbook: API Latency Degradation

**Service:** payment-api
**Severity:** SEV-2 (SEV-1 if p99 > 5s or error rate climbing)
**Category:** Performance

## Symptoms
- p95/p99 latency on `/v1/payments` and `/v1/refunds` endpoints exceeds SLO (baseline p95: 250ms, alert threshold: 800ms)
- Client-side timeouts reported by upstream services (checkout-service, order-service)
- Grafana `api-latency` dashboard shows a sustained upward trend over 10+ minutes, not a single spike
- Request queue depth on the API gateway is increasing
- CPU utilization on payment-api pods trending toward 80-90%

## Probable Causes
1. Traffic spike beyond current replica count (organic growth, retry storms from a downstream failure, or a marketing event)
2. A recent deployment introduced a slow code path (N+1 query, unbounded loop, synchronous call added on the hot path)
3. Downstream dependency (Postgres, Redis, a third-party processor) is itself slow, and payment-api is blocking on it
4. Garbage collection pauses due to memory pressure (see companion runbook: memory-leak-oomkilled)
5. Noisy-neighbor CPU throttling on a shared node

## Diagnostics
1. Check the latency breakdown by endpoint in Grafana — is it all endpoints or one? An isolated endpoint points to cause #2; broad degradation points to #1, #3, or #5.
2. `kubectl top pods -n payments` — confirm whether CPU/memory is actually saturated or if pods are idle-but-slow (idle-but-slow usually means blocked on I/O, i.e. a downstream dependency).
3. Check `run_diagnostics` output for the payment-api deployment: recent restarts, throttling events, and current replica count vs HPA target.
4. Query APM traces (Datadog/Jaeger) for the slowest 1% of requests in the last 15 minutes — look for a single span dominating the trace (DB call, external HTTP call, lock wait).
5. Check Postgres `pg_stat_activity` for long-running or queued queries; check Redis `INFO commandstats` and `SLOWLOG GET` for slow commands.
6. Diff the last deployment's changelog against the time the degradation started — correlate with `kubectl rollout history`.
7. Check node-level metrics for CPU steal time (`cpu.steal` in the node exporter) to rule out noisy-neighbor throttling.

## Remediation
- **If traffic-driven:** trigger `scale_service` to increase replica count immediately; verify HPA min/max bounds aren't capping it.
- **If downstream-driven:** use `invalidate_cache` if a stale-cache pattern is causing cache-miss fallthrough to the DB (see cache-miss-storm runbook); otherwise open an incident with the owning team for the slow dependency and apply a circuit breaker / timeout reduction on payment-api's client to fail fast instead of queuing.
- **If deployment-driven:** roll back via `kubectl rollout undo deployment/payment-api -n payments`, then re-deploy the fix behind a feature flag.
- **If DB-driven:** identify and kill blocking queries (`pg_terminate_backend`), and flush the connection pool with the DB pool flush tool if connections are exhausted rather than just slow.
- **If node-level throttling:** cordon the affected node and let the scheduler redistribute pods; file a ticket with platform team about node sizing.

## Verification
- p95 latency returns below 400ms for 10 consecutive minutes
- Request queue depth returns to baseline
- No new timeout errors from upstream services in the last 5 minutes

## Escalation
- If p99 exceeds 5s or error rate exceeds 5%, page the on-call payments engineer and open a SEV-1.
- If the root cause is a third-party payment processor outage, notify the incident commander and prepare a customer-facing status page update.

## Related Runbooks
- db-pool-exhaustion
- cache-miss-storm
- memory-leak-oomkilled

# Runbook: Memory Leak / OOMKilled Pods

**Service:** payment-api
**Severity:** SEV-2 (SEV-1 if restart loop causes availability drop)
**Category:** Resource Exhaustion

## Symptoms
- `kubectl get pods` shows pods with `OOMKilled` in the last termination reason
- Restart count climbing on one or more pods over hours/days
- Memory usage graph shows a steady upward "sawtooth" pattern: climbs until OOMKilled, resets on restart, climbs again
- Increased p99 latency shortly before each OOMKill (GC pressure)
- Alertmanager firing `PodMemoryHigh` or `ContainerRestartingFrequently`

## Probable Causes
1. A genuine memory leak in application code (unclosed connections, growing in-memory cache with no eviction, accumulating event listeners)
2. Memory limit set too low for legitimate peak load (not a leak, just under-provisioned)
3. A large or unbounded response being buffered fully in memory before streaming
4. A dependency library holding references longer than expected (e.g., an HTTP client connection pool not releasing sockets)
5. Batch job or background task processing large payloads without pagination

## Diagnostics
1. `kubectl describe pod <pod> -n payments` — confirm `OOMKilled` (exit code 137) and check the memory limit vs request.
2. Pull the memory usage timeseries from Grafana for the affected pod over the last 24-72h — a strictly monotonic climb (ignoring restarts) confirms a leak; a plateau near the limit under load suggests under-provisioning instead.
3. If a heap dump/profiler is available (e.g., `py-spy dump`, Node `--inspect`, JVM heap dump), capture one shortly before an expected OOMKill and inspect for retained object growth.
4. Check `run_diagnostics` for correlation between OOMKills and specific request patterns (e.g., large file uploads, bulk export endpoint calls).
5. Review recent code changes for newly introduced caches, queues, or buffers without size limits or TTLs.
6. Check whether OOMKills correlate with a specific replica only (suggests a stuck long-lived connection on that pod) or all replicas evenly (suggests a systemic leak).

## Remediation
- **Immediate mitigation:** increase the memory limit as a stopgap via deployment patch, and/or reduce restart impact by increasing replica count with `scale_service` to spread load.
- **If a specific endpoint is implicated:** add pagination/streaming to that endpoint and disable or rate-limit it temporarily if it is non-critical.
- **If a leak is confirmed in a cache:** add an eviction policy (LRU with max size or TTL) and deploy.
- **If connection pooling is the cause:** audit client library configuration for max idle connections and connection lifetime, and ensure `close()`/context managers are used consistently.
- Restart affected pods proactively (`kubectl rollout restart deployment/payment-api`) to buy time while the fix is developed, but do not treat this as a resolution.

## Verification
- Memory usage graph flattens to a stable baseline under representative load for at least one full traffic cycle (e.g., 24h including peak hours)
- Restart count stops increasing
- No OOMKilled events in the last 24h post-fix

## Escalation
- If OOMKills are causing more than one pod to be unavailable simultaneously and reducing capacity below safe headroom, page on-call and consider temporarily disabling the implicated feature.

## Related Runbooks
- api-latency-degradation
- db-pool-exhaustion
# Runbook: Disk Space Exhaustion

**Service:** payment-api / postgres-primary
**Severity:** SEV-1 (write failures imminent or in progress)
**Category:** Storage

## Symptoms
- Alert: `DiskSpaceLow` or `DiskSpaceCritical` (>90% used) on a node or PVC
- Postgres logs show `could not write to file` or `no space left on device`
- Application writes (logs, temp files, uploads) begin failing
- `kubectl get pvc` shows a volume near its capacity limit
- Postgres may enter read-only mode or refuse new connections if WAL cannot be written

## Probable Causes
1. Unbounded log growth (verbose logging left on, or a logging bug producing duplicate/looping entries)
2. Postgres WAL accumulation because a replication slot or backup process is stalled and preventing WAL cleanup
3. Temp file accumulation from failed or long-running queries
4. Old backups, core dumps, or uploaded files never cleaned up
5. A runaway process writing large amounts of data unexpectedly (e.g., a debug dump left enabled in production)

## Diagnostics
1. `df -h` on the affected node, or `kubectl exec` into the pod and check `du -sh /*` to find the largest directories.
2. For Postgres specifically: check `pg_wal` directory size and compare to `pg_replication_slots` — an inactive or stuck slot is a common cause of WAL bloat.
3. Check log rotation configuration — confirm `logrotate` or the container's log driver is actually rotating and compressing.
4. List recent large files: `find / -xdev -type f -size +100M -mmin -1440` to catch anything written in the last 24h.
5. Check backup job status — a failed backup can leave a stale replication slot or snapshot lock that blocks cleanup.

## Remediation
- **Immediate:** free space fast — remove or compress old logs, clear temp directories, delete completed and verified backup artifacts that are duplicated elsewhere.
- **If a stuck replication slot is the cause:** identify the inactive slot with `SELECT * FROM pg_replication_slots WHERE active = false;` and drop it if it's confirmed abandoned (`SELECT pg_drop_replication_slot('slot_name');`) — verify with the DBA/on-call before dropping if any doubt about whether it's in use.
- **If logging is the cause:** reduce log level temporarily, patch the logging bug, and confirm rotation is active.
- **Structural fix:** expand the PVC size (`kubectl patch pvc ... -p '{"spec":{"resources":{"requests":{"storage":"<new size>"}}}}'` for volumes that support online expansion), and add a disk-usage alert well before critical threshold (e.g., at 70%) going forward.

## Verification
- Disk usage drops below 70% and stabilizes
- Postgres accepts writes normally, WAL directory size returns to expected range
- No recurring growth trend in the following 24h

## Escalation
- If Postgres has already entered read-only/refuse-connections state, this is a SEV-1 — page on-call DBA immediately before attempting slot drops or file deletion in the data directory.

## Related Runbooks
- db-pool-exhaustion
- backup-restore-failure
# Runbook: TLS Certificate Expiration

**Service:** payment-api (ingress) / internal service mesh
**Severity:** SEV-1 (external-facing) / SEV-2 (internal mTLS)
**Category:** Security / Connectivity

## Symptoms
- Clients report `SSL_ERROR`, `CERT_HAS_EXPIRED`, or `NET::ERR_CERT_DATE_INVALID`
- Sudden, complete traffic drop to zero for a specific hostname while other services remain healthy
- Internal service-to-service calls failing with mTLS handshake errors (if using a service mesh like Istio/Linkerd)
- Monitoring alert `CertExpiringSoon` was likely fired days earlier and missed or unactioned

## Probable Causes
1. Manual certificate renewal process was missed (no automation, or automation silently failed)
2. cert-manager (if used) failed to renew due to a misconfigured ACME issuer, rate limiting from the CA, or a DNS-01/HTTP-01 challenge failure
3. Certificate was renewed but not reloaded into the ingress controller or the pod (stale cert still being served)
4. Intermediate CA certificate expired even though the leaf certificate is valid, breaking the chain of trust

## Diagnostics
1. Check expiry directly: `openssl s_client -connect <host>:443 -servername <host> </dev/null 2>/dev/null | openssl x509 -noout -dates`
2. If using cert-manager: `kubectl get certificate -A` and `kubectl describe certificate <name>` — check `Status.Conditions` for renewal failures.
3. Check cert-manager logs for ACME challenge errors: `kubectl logs -n cert-manager deploy/cert-manager`
4. Confirm whether a new cert secret exists but the ingress/pod hasn't picked it up: compare the secret's `not-after` date to what's actually being served over the wire (these can differ if a reload didn't happen).
5. Check the CA's rate-limit status if using Let's Encrypt — repeated failed attempts can trigger a temporary block.

## Remediation
- **If expired with no renewal in progress:** manually issue an emergency certificate (via the CA's manual process or a temporary self-managed cert while automation is fixed) and apply it directly to unblock traffic.
- **If cert-manager renewed but the pod didn't reload:** force a rolling restart of the ingress controller or affected pods to pick up the new secret (`kubectl rollout restart deployment/<ingress-controller>`).
- **If the ACME challenge is failing:** check DNS records (for DNS-01) or that the HTTP-01 challenge path is reachable (not blocked by a WAF or auth middleware).
- **If rate-limited by the CA:** wait out the rate-limit window while using a temporary certificate from an alternate source, and stagger renewal schedules going forward to avoid retriggering it.
- Once resolved, verify certificate auto-renewal is scheduled well before expiry (30 days out is standard) and that an alert fires at 14 and 3 days remaining.

## Verification
- `openssl s_client` confirms the new expiry date is in the future and matches the expected certificate
- Client error rate for SSL/TLS errors returns to zero
- mTLS handshake success rate (if applicable) returns to 100%

## Escalation
- Expired external-facing certificates are customer-visible immediately — treat as SEV-1, notify the incident commander, and prepare a status page update even while the fix is in progress.

## Related Runbooks
- api-latency-degradation
- network-partition-timeout
# Runbook: Message Queue Backlog

**Service:** payment-events (Kafka/RabbitMQ) consumers
**Severity:** SEV-2 (SEV-1 if backlog risks data loss via TTL/retention expiry)
**Category:** Async Processing

## Symptoms
- Consumer lag alert fires (`ConsumerLagHigh`) — lag measured in messages or time climbing steadily
- Downstream effects delayed: payment confirmation emails, webhook notifications, or reconciliation reports arrive late
- Queue depth (RabbitMQ) or partition lag (Kafka) growing faster than it's being drained
- Consumers appear "up" (not crashed) but throughput is far below production rate

## Probable Causes
1. Consumer processing got slower (e.g., a downstream call added synchronously per message, or a DB write per message became slower — see api-latency-degradation)
2. Consumer group has fewer active instances than expected (a pod crashed or was scaled down and didn't recover)
3. A "poison pill" message is repeatedly failing and blocking the partition/queue (no dead-letter handling, or retry loop without backoff)
4. Producer rate spiked well above normal (e.g., a batch job or replay dumped a large volume of messages at once)
5. Partition/queue rebalancing storm causing repeated consumer group reassignments and stalled processing

## Diagnostics
1. Check consumer lag per partition/queue, not just aggregate — an even lag increase across all partitions points to #1/#2/#4; a single stuck partition points to #3.
2. `kubectl get pods -n payments -l app=payment-consumer` — confirm the expected number of consumer replicas are running and not crash-looping.
3. Check consumer logs for repeated processing of the same message ID/offset (a hallmark of a poison pill stuck in a retry loop).
4. Compare current producer throughput (messages/sec) against baseline to rule out a legitimate volume spike.
5. Check for rebalance events in consumer group logs (Kafka: `JoinGroup`/`SyncGroup` churn) which can indicate flapping consumers rather than a processing issue.

## Remediation
- **If under-provisioned:** `scale_service` to add consumer replicas (verify this is safe for ordering guarantees if the topic/queue requires strict ordering).
- **If a poison pill is blocking a partition:** identify the offending message, move it to a dead-letter queue manually if no DLQ handling exists, and manually advance the consumer offset past it (only after confirming the message is truly unprocessable, not just slow).
- **If a downstream slowdown is the root cause:** apply the latency-degradation runbook for that dependency; consider making the consumer's downstream call async/batched to reduce per-message overhead in the meantime.
- **If a legitimate volume spike:** let consumers catch up naturally if within retention/TTL window; scale up temporarily to accelerate drain.
- Add DLQ handling and exponential backoff with a max-retry cap if not already present, to prevent future poison-pill blocking.

## Verification
- Consumer lag trends back to near-zero and stays flat (not just briefly dipping)
- No repeated offset/message-ID processing in logs
- Downstream effects (notifications, reconciliation) resume near-real-time

## Escalation
- If lag approaches the topic's retention window or queue TTL, this becomes a data-loss risk — escalate to SEV-1 and prioritize scaling/unblocking over root-causing.

## Related Runbooks
- api-latency-degradation
- db-pool-exhaustion
# Runbook: DNS Resolution Failures

**Service:** cluster-wide (CoreDNS) / payment-api dependencies
**Severity:** SEV-1 (cluster-wide) / SEV-2 (single dependency)
**Category:** Networking

## Symptoms
- Intermittent or total `getaddrinfo ENOTFOUND` / `Temporary failure in name resolution` errors in application logs
- Errors are inconsistent — some requests succeed, others fail, often correlating with specific pods or nodes
- Service-to-service calls fail even though the target service is confirmed healthy
- CoreDNS pods show high restart count, CPU throttling, or OOMKills
- External API calls (e.g., to a payment processor) fail to resolve while internal cluster DNS is fine, or vice versa

## Probable Causes
1. CoreDNS pods are overloaded, under-provisioned, or crash-looping under query volume
2. `ndots` misconfiguration causing excessive DNS query fan-out per lookup (a common Kubernetes default gotcha — 5 queries per lookup instead of 1)
3. Upstream DNS resolver (for external names) is unreachable or rate-limiting
4. A NetworkPolicy or firewall change inadvertently blocked traffic to the DNS service
5. `/etc/resolv.conf` misconfigured in a pod's image or overridden by custom `dnsConfig`

## Diagnostics
1. `kubectl get pods -n kube-system -l k8s-app=kube-dns` — check restarts and resource usage against limits.
2. `kubectl logs -n kube-system -l k8s-app=kube-dns` — look for `SERVFAIL`, timeout, or upstream errors.
3. From inside an affected pod: `nslookup <target-service>` and `cat /etc/resolv.conf` — check `ndots` value and search domains.
4. Check CoreDNS query rate/latency metrics in Grafana (`coredns_dns_requests_total`, `coredns_dns_request_duration_seconds`) for saturation.
5. Confirm whether the failure is internal-only, external-only, or both — this quickly narrows to cause #1/#2 (internal) vs #3 (external).
6. Check recent NetworkPolicy changes with `kubectl get networkpolicy -A` and diff against the incident start time.

## Remediation
- **If CoreDNS is overloaded:** scale CoreDNS replicas (`scale_service` equivalent for the kube-dns deployment) and confirm autoscaling (if configured) has adequate headroom; increase CPU/memory limits if throttling is observed.
- **If `ndots` fan-out is the cause:** add a trailing dot to fully-qualified external hostnames in application config, or tune the pod's `dnsConfig.options` to reduce `ndots` for services making heavy external calls.
- **If upstream resolver issues:** switch to a secondary upstream DNS temporarily via CoreDNS's `forward` plugin config, and open a ticket with the network/cloud provider.
- **If a NetworkPolicy is blocking DNS:** add an explicit allow rule for UDP/TCP port 53 to the kube-dns service, then investigate why it was omitted.
- Restart affected CoreDNS pods only as a last resort after root cause is identified, since a blind restart can mask the underlying capacity issue.

## Verification
- `nslookup` succeeds consistently from previously affected pods
- CoreDNS error rate and query latency return to baseline
- No new `ENOTFOUND`/timeout errors in application logs for 15+ minutes

## Escalation
- Cluster-wide DNS failure affects every service simultaneously — treat as SEV-1 and page the platform/infra on-call immediately, since this blocks nearly all other diagnostics and remediations too.

## Related Runbooks
- network-partition-timeout
- api-latency-degradation
# Runbook: Pod CrashLoopBackOff

**Service:** payment-api / any Kubernetes-deployed service
**Severity:** SEV-2 (SEV-1 if it drops available replicas below safe minimum)
**Category:** Deployment / Availability

## Symptoms
- `kubectl get pods` shows one or more pods in `CrashLoopBackOff` status
- Restart count increasing rapidly, with exponentially increasing backoff delay between attempts
- Reduced available replica count; possible increased load/latency on remaining healthy pods
- Alert `PodCrashLooping` firing

## Probable Causes
1. Application-level startup failure: unhandled exception on boot, missing environment variable, bad config value
2. Failing readiness/liveness probe causing Kubernetes to kill the pod before it finishes starting (probe timeout too aggressive for actual startup time)
3. Missing or misconfigured dependency at startup (can't reach DB/Redis, required secret/configmap missing)
4. OOMKilled at startup specifically (large in-memory init, e.g., loading a big model or cache eagerly) — see memory-leak-oomkilled for the general pattern, but startup-time OOM is often a limit-sizing issue rather than a leak
5. Image/tag mismatch — a bad image was deployed (e.g., wrong build, corrupted layer)

## Diagnostics
1. `kubectl describe pod <pod> -n payments` — check `Last State` reason (`Error`, `OOMKilled`, etc.) and exit code.
2. `kubectl logs <pod> -n payments --previous` — get logs from the crashed instance, not the newly restarting one.
3. Compare exit code: 1 = application error, 137 = OOMKilled/SIGKILL, 143 = SIGTERM (often from a probe failure or graceful shutdown timeout).
4. Check `kubectl get events -n payments --sort-by=.lastTimestamp` for probe failure events (`Liveness probe failed`, `Readiness probe failed`).
5. Diff the deployment's current image tag/digest against the last known-good one; check if this correlates with a recent rollout (`kubectl rollout history deployment/payment-api`).
6. Verify all referenced ConfigMaps/Secrets/env vars exist and are correctly named: `kubectl exec` into a healthy sibling pod (if any) and diff environment.

## Remediation
- **If a bad deploy:** roll back immediately: `kubectl rollout undo deployment/payment-api -n payments`.
- **If probe misconfiguration:** increase `initialDelaySeconds` and/or `failureThreshold` on the liveness/readiness probe to match actual startup time, then redeploy.
- **If a missing dependency at startup:** verify the dependency (DB, Redis, etc.) is actually reachable and healthy first — fix that in parallel using its own runbook (e.g., db-pool-exhaustion), since redeploying the app won't help if the dependency is down.
- **If missing config/secret:** restore or correct the ConfigMap/Secret and restart the deployment.
- **If startup-time OOM:** increase the memory limit for now, and file a follow-up to make the expensive initialization lazy or streamed rather than fully eager.

## Verification
- `kubectl get pods` shows all replicas in `Running` and `Ready` state with restart count stable (not climbing)
- No new `CrashLoopBackOff` events for at least 15 minutes
- Readiness checks passing consistently

## Escalation
- If the crash loop affects all replicas of a critical service simultaneously (full outage), treat as SEV-1 and page on-call immediately — do not wait for root-cause before rolling back.

## Related Runbooks
- memory-leak-oomkilled
- api-latency-degradation
# Runbook: Downstream Rate Limiting (429 Errors)

**Service:** payment-api → third-party payment processor
**Severity:** SEV-2 (SEV-1 if payment success rate drops materially)
**Category:** External Dependency

## Symptoms
- A sudden rise in HTTP 429 responses from a downstream API (e.g., payment processor, fraud-check service)
- Payment success rate drops while overall traffic to payment-api stays normal or only slightly elevated
- Retry storms visible in logs — the same request ID retried many times in a short window
- Downstream provider's status page may show throttling notices or degraded performance

## Probable Causes
1. Traffic to the downstream service exceeded the contracted rate limit (organic growth outpacing the current tier/quota)
2. A retry-without-backoff bug is amplifying the effective request rate far beyond actual user traffic (a self-inflicted storm)
3. A batch job or bulk operation ran concurrently with normal traffic, consuming shared rate-limit headroom
4. The downstream provider changed or tightened its rate limits without notice
5. Multiple services share the same API key/quota, and another internal service is the actual source of the increased volume

## Diagnostics
1. Check the ratio of unique requests vs total requests (including retries) sent downstream in the affected window — a large gap indicates a retry storm (#2) rather than genuine volume (#1/#3).
2. Check the downstream provider's response headers (`X-RateLimit-Remaining`, `Retry-After`) to confirm which quota is being exhausted and when it resets.
3. Review recent deployments to the client code calling the downstream API for retry logic changes (missing exponential backoff, retry-on-429 without a cap).
4. If the API key/quota is shared across services, check other services' request volume for the same time window to identify the actual source.
5. Check for scheduled batch jobs (reconciliation, reporting) that overlap with the incident window.

## Remediation
- **If a retry storm:** deploy a fix to add exponential backoff with jitter and a max-retry cap; in the meantime, apply a circuit breaker to stop retrying immediately and fail fast, reducing load on the downstream quota so real traffic can get through.
- **If genuine volume growth:** contact the provider to request a quota increase, and apply client-side rate limiting/queuing on payment-api's outbound calls to smooth bursts below the limit while the increase is negotiated.
- **If a batch job is the cause:** pause or reschedule the batch job to a lower-traffic window, and apply separate rate-limit budgets per use case if the provider supports it.
- **If it's a shared-quota conflict:** request quota partitioning (separate keys per service) from the provider so one service's traffic pattern can't starve another's.
- While mitigating, prioritize queuing/deferring non-critical calls (e.g., fraud re-checks) over critical ones (e.g., payment authorization).

## Verification
- 429 rate drops to zero or negligible baseline
- Payment success rate returns to baseline (typically >99%)
- `X-RateLimit-Remaining` stays comfortably above zero for a full traffic cycle

## Escalation
- If payment success rate drops below an agreed threshold (e.g., 95%) for more than 5 minutes, escalate to SEV-1, notify the incident commander, and consider temporarily disabling non-essential downstream calls to preserve quota for payment authorization specifically.

## Related Runbooks
- api-latency-degradation
- message-queue-backlog
# Runbook: Network Partition / Service Mesh Timeout

**Service:** payment-api ↔ internal services (via Istio/Linkerd service mesh)
**Severity:** SEV-1 (if cross-service calls broadly failing)
**Category:** Networking

## Symptoms
- Intermittent or total connection timeouts between specific services that were previously communicating fine
- Errors like `upstream connect error or disconnect/reset before headers` (Envoy) or generic `connection refused`/`timeout` at the application layer
- The failure is asymmetric: Service A can reach Service B, but Service B cannot reach Service A, or only calls crossing an availability zone/node boundary fail
- Sidecar proxy (Envoy) logs show connection resets or circuit breaker trips (`503 UO` / upstream overflow)
- Health checks between mesh-enabled services fail while direct pod-IP checks (bypassing the mesh) may succeed

## Probable Causes
1. A genuine network partition between nodes or availability zones (cloud provider network issue, or a misconfigured CNI)
2. Service mesh sidecar (Envoy) circuit breaker tripped due to exceeding configured connection pool limits, mistaken for a "network" issue
3. mTLS certificate mismatch or rotation issue within the mesh (see also tls-certificate-expiration, but mesh-internal certs are a distinct rotation path)
4. A NetworkPolicy change unintentionally blocked traffic between specific namespaces/pods
5. DNS resolution failure between services being misdiagnosed as a network partition (see dns-resolution-failure — rule this out first)

## Diagnostics
1. Rule out DNS first: confirm the target service resolves correctly from the calling pod before assuming a network-layer issue.
2. Check sidecar proxy stats: `istioctl proxy-config clusters <pod>` (Istio) to see circuit breaker state and connection pool utilization for the failing upstream.
3. Check mesh mTLS status: `istioctl authn tls-check <pod>.<namespace>` to confirm certs are valid and mTLS mode matches on both sides (STRICT vs PERMISSIVE mismatches cause silent failures).
4. Test connectivity bypassing the mesh (direct pod IP, no sidecar) to isolate whether the mesh itself or the underlying network is at fault.
5. Check `kubectl get networkpolicy -A` for recent changes and confirm they weren't the cause via timestamp correlation.
6. Check cloud provider status page for known AZ/network incidents in the relevant region.

## Remediation
- **If a circuit breaker trip (not a real partition):** the real fix is capacity — increase connection pool limits in the mesh config (`DestinationRule` in Istio) or scale the target service; do not just increase timeouts, which masks the underlying saturation.
- **If mTLS mismatch:** align the mTLS mode across both services' `PeerAuthentication`/`DestinationRule` config, and force a cert rotation if certs are stale (`istioctl proxy-config secret <pod>` to check current cert expiry).
- **If a NetworkPolicy regression:** revert the policy change or add the missing explicit allow rule, then re-apply the intended restriction more carefully.
- **If a genuine cloud-provider network partition:** there is often no application-side fix — fail over to a healthy AZ if the architecture supports it, and monitor the provider's status page for resolution.
- **If DNS was actually the cause:** follow the dns-resolution-failure runbook instead.

## Verification
- Cross-service calls succeed consistently across all affected paths (test both directions, and across AZs if applicable)
- Sidecar proxy circuit breakers show healthy/closed state
- No new connection reset or timeout errors for 15+ minutes

## Escalation
- If the partition affects payment-critical service-to-service calls broadly, treat as SEV-1 and loop in the platform/networking on-call in parallel with application-level diagnostics, since root cause may sit outside application code entirely.

## Related Runbooks
- dns-resolution-failure
- tls-certificate-expiration
- api-latency-degradation
# Runbook: Failed Deployment / Rollback Required

**Service:** payment-api
**Severity:** SEV-2 (SEV-1 if the failed deployment is causing customer-facing errors)
**Category:** Deployment

## Symptoms
- Error rate, latency, or crash rate increases immediately following a deployment (correlate timestamps first — this is the single most important diagnostic signal)
- New pods fail readiness checks and never reach `Ready` state, while old pods are terminated per the rollout strategy (potential availability gap if `maxUnavailable` is too permissive)
- Feature-specific errors appear that map directly to the changelog of the new release
- Alert `DeploymentRolloutStuck` or `ErrorRateHigh` firing within minutes of a known deploy time

## Probable Causes
1. A genuine code defect shipped in the new release (logic bug, unhandled edge case, broken integration)
2. A required migration (DB schema change, config change) wasn't applied before the new code that depends on it
3. A configuration or secret that the new version expects wasn't provisioned in the target environment
4. Backward-incompatible change to an API contract that breaks a caller still running the old client version (common during rolling deploys where old and new versions run simultaneously)
5. Resource requests/limits changed in the new manifest and the cluster can't schedule the new pods (unrelated to app logic, but presents similarly)

## Diagnostics
1. Confirm timing correlation: `kubectl rollout history deployment/payment-api -n payments` and compare the rollout timestamp to the alert/error start time precisely — a few minutes' gap can mean the cause is unrelated.
2. `kubectl logs <new-pod>` for application-level errors, and `kubectl describe pod <new-pod>` for scheduling/resource issues if pods are stuck `Pending`.
3. Diff the changelog/PR list included in the release against the specific error signatures being seen — map each new error type to a specific code change if possible.
4. If schema-related, check whether the migration step actually ran and succeeded before the app deploy (check migration job logs/status separately).
5. If this is a rolling deploy, check whether errors are happening only on new-version pods, only on old-version pods (contract break, cause #4), or both.

## Remediation
- **Default first action for any deployment-correlated incident:** roll back immediately rather than attempting a forward-fix under pressure: `kubectl rollout undo deployment/payment-api -n payments`. Root-cause after stability is restored.
- **If a migration is missing:** do not roll back the schema if data has already been written in the new format — instead, expedite running the missing migration, or roll back only the application code while keeping the schema change (verify backward compatibility of the old code with the new schema first).
- **If a config/secret is missing:** provision it and re-attempt the deploy rather than rolling back, if the fix is fast and low-risk; otherwise roll back and fix forward.
- **If it's an API contract break during a rolling deploy:** pause the rollout (`kubectl rollout pause`) to stop mixing versions further, decide whether to complete the rollout quickly (if the new version is otherwise correct) or roll back, based on blast radius.
- **If a resource scheduling issue:** correct the manifest's requested resources or add cluster capacity, then retry the rollout.

## Verification
- Error rate and latency return to pre-deploy baseline
- All pods reach `Ready` state at the previous (or newly fixed) version, with restart counts stable
- No customer-facing error reports for at least 15 minutes post-rollback/fix

## Escalation
- If the bad deployment already affected customer transactions (e.g., failed payments, duplicate charges), this requires immediate SEV-1 handling, incident commander involvement, and likely a data-integrity review in addition to the technical rollback.

## Related Runbooks
- pod-crashloopbackoff
- api-latency-degradation