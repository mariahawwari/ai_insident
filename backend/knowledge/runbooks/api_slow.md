# API Slowness Runbook

## Symptoms
- API responses take longer than usual.
- Users report delays or timeouts.
- Monitoring shows increased latency.

## Quick Checks
- Check recent deployments.
- Check traffic spikes.
- Check database performance.

## Possible Causes
- Slow database queries.
- External service delays.
- High CPU or memory usage.
- Too many requests hitting the API.

## Actions
- Restart the API service if CPU is stuck.
- Scale the API horizontally.
- Optimize slow database queries.
- Add caching for expensive operations.

## Safety Notes
- Do NOT restart during peak traffic without approval.
- Do NOT change database indexes without review.
