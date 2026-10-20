# Database Errors Runbook

## Symptoms
- API returns 500 errors.
- Logs show "database connection failed".
- Slow queries or timeouts.

## Quick Checks
- Check database CPU and memory.
- Check number of active connections.
- Check if a migration recently ran.

## Possible Causes
- Connection pool exhausted.
- Long-running queries blocking others.
- Database restarted or crashed.
- Network issues between API and DB.

## Actions
- Increase connection pool size.
- Kill long-running queries.
- Restart the database if unresponsive.
- Check recent migrations for issues.

## Safety Notes
- Do NOT kill queries without checking the owner.
- Do NOT restart the database during peak hours.
