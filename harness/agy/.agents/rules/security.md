# Security and Safety Rules

The agent must strictly adhere to the following safety constraints:

## Protected Files (Deny Access)
Never read, write, or modify secrets or environment configuration files:
- `.env`
- `**/.env`
- `**/.env.local`
- `**/.env.production`
- `**/.env.staging`
- `**/credentials.json`
- `**/service-account.json`

## Mutation Approvals (Require Explicit User Confirmation)
Do not automatically run destructive or remote infrastructure mutations without explicit confirmation:
- `git push`
- `terraform apply`, `terraform destroy`, `terraform import`
- `kubectl apply`, `kubectl delete`, `kubectl replace`, `kubectl patch`
