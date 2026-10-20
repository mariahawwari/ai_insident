
# Redis Memory Issue Runbook

## What is happening
Redis is a fast temporary storage system.  
When it runs out of memory, the application becomes slow or shows errors.

## Symptoms
- The system feels slow.
- Users see random errors.
- Logs mention “Redis” or “OOM”.
- The cache hit rate drops.

## Why this happens
- Redis is full.
- Too many items stored.
- Items are too large.
- Items are not expiring.

## What to check
1. Look at monitoring dashboards.
2. Check if traffic increased.
3. Check if a new deployment changed caching.

## What to do
### Case A — Redis is unhealthy
- Restart the Redis service.
- Reduce the size of cached items.
- Escalate to the SRE team.

### Case B — Redis is healthy but full
- Add expiration (TTL) to cached items.
- Remove unnecessary cached data.
- Reduce the amount of data stored.

## Safety
- Do NOT delete everything at once.
- Do NOT flush Redis during peak traffic.
- Always confirm with the team before big changes.
