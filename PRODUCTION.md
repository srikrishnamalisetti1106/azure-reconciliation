# Running this in production on Azure

The demo runs free (Databricks Free Edition + a laptop). This file explains how the
same code runs in a real Azure setup, and which file in the repo does each part.
Nothing in the matching rules, tests or assistant logic changes: production swaps
**where data comes from, where code runs, and who is allowed in**.

> Status: these files are written to be correct but have **not been deployed**.
> `terraform validate`, `databricks bundle validate` and `docker build` have not been
> run against a real subscription. Expect small fixes on first use.

## Demo vs production

| Concern | Demo (free) | Production (Azure) | File |
|---|---|---|---|
| Raw files | Uploaded to a Databricks volume | Land in ADLS Gen2 `landing` container | `infra/terraform/main.tf` |
| Ingestion | Read all CSVs each run | Auto Loader reads only new files, with checkpoints | `databricks/prod/01_bronze_autoloader.py` |
| Re-runs | Overwrite tables | Delta MERGE, so re-sent files don't double count | `databricks/prod/02_silver_merge.py`, `03_gold_reconcile.py` |
| Bad rows | Assert fails the run | Quarantine table with the failed check's name | `02_silver_merge.py` |
| Exceptions | Recomputed each run | Tracked over time: OPEN, then RESOLVED when fixed | `03_gold_reconcile.py` |
| KPIs | Table | Append-only snapshots + `kpi_latest` view | `04_kpi_snapshots.py` |
| Scheduling | Run notebooks by hand | Job triggered when a file arrives, failure emails | `databricks.yml` |
| Environments | One workspace | `dev` and `prod` bundle targets, separate catalogs | `databricks.yml` |
| Assistant data | Local DuckDB copy | Databricks SQL warehouse, read-only service principal | `assistant/tools_databricks.py` |
| Secrets | `.env` on a laptop | Key Vault, read by a managed identity | `main.tf` (Container App `secret` blocks) |
| API hosting | `uvicorn` locally | Docker image in ACR, Azure Container Apps (scales to zero) | `Dockerfile`, `main.tf` |
| Sign-in | None | Entra ID tokens with app roles `Recon.Read`, `Recon.Investigate` | `api/auth.py` |
| Deploys | `git push` | GitHub Actions with OIDC login and a manual approval gate | `.github/workflows/deploy.yml` |

## How a file flows through production

1. The processor and the bank drop files into `landing/payments/`, `landing/ledger/`, `landing/fees/`.
2. The file-arrival trigger starts the `reconciliation-daily` job.
3. **Bronze**: Auto Loader appends only the new files, with `_source_file` and `_ingested_at`.
4. **Silver**: types are cast, failing rows go to `*_quarantine`, duplicates keep the latest load, MERGE into silver.
5. **Gold**: `reconciliation/matching.py` (the code CI tests) runs. New matches are inserted;
   exceptions are upserted, and ones that disappeared are marked RESOLVED. A control check fails the
   run if any payment is neither matched nor in an open exception.
6. **Reporting**: KPI snapshots are appended. Power BI (or Tableau) reads `kpi_latest` through a SQL warehouse.
7. **Assistant**: analysts call the API with an Entra token. The agent's tools query gold/reporting
   with fixed, parameterized SQL as a service principal that only has `SELECT`.

## Security model (the interview answer)

- **No secrets in code or GitHub.** The API reads the Anthropic key and Databricks token from Key Vault
  via a managed identity. GitHub logs in to Azure with OIDC, so there is no stored Azure password.
- **Least privilege.** Analysts get `SELECT` on gold and reporting only (`00_setup.py`). The assistant's
  identity is read-only. Only the pipeline's service principal writes.
- **The model can't write SQL.** It picks one of four fixed queries and passes IDs as parameters.
  The tool subprocess doesn't even receive the Anthropic key (`agent.py` filters its environment).
- **Every answer is attributable.** The API records who asked (`asked_by`), Langfuse traces each tool call,
  and every gold row carries `run_id` and `rule_id`.

## One-time setup order

1. Create a resource group and storage account for Terraform state; uncomment the `backend` block.
2. `cd infra/terraform && cp dev.tfvars.example dev.tfvars` and fill it in, then
   `terraform init && terraform plan -var-file=dev.tfvars && terraform apply -var-file=dev.tfvars`.
3. Add secrets: `az keyvault secret set --vault-name <kv> --name anthropic-api-key --value ...`
   (and `databricks-token`).
4. In Databricks: create a storage credential from the access connector, an external location for
   `landing_url`, the catalog, a SQL warehouse, and the `recon-analysts` group.
5. Register an Entra app for the API with app roles `Recon.Read` and `Recon.Investigate`; put its client ID in tfvars.
6. Create an Entra app for GitHub with a federated credential for this repo's `production` environment,
   then add the variables listed at the top of `deploy.yml`.
7. Push to `main`: CI runs, then deploy waits for approval, then rolls out infrastructure, image and jobs.

## Cost guardrails

Set a budget alert first (Cost Management, e.g. $5). Container Apps scales to zero, ACR Basic is
about $5/month, and Databricks charges only while the job or SQL warehouse runs (use serverless and
auto-stop). Run `terraform destroy -var-file=dev.tfvars` when you're done experimenting.
