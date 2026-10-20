# Login Failures Runbook

## Symptoms
- Users cannot log in.
- API returns 401 or 403 errors.
- Authentication service unreachable.

## Quick Checks
- Check authentication service status.
- Check recent deployments.
- Check user database availability.

## Possible Causes
- Token service down.
- Incorrect credentials stored.
- Database connection issues.
- Rate limiting blocking login attempts.

## Actions
- Restart authentication service.
- Clear invalid tokens.
- Fix database connectivity.
- Reduce rate limiting thresholds temporarily.

## Safety Notes
- Do NOT disable authentication checks.
- Do NOT expose user credentials in logs.
