variable "project" {
  description = "Short project name used in resource names."
  type        = string
  default     = "recon"
}

variable "environment" {
  description = "dev, test or prod."
  type        = string
  default     = "dev"
}

variable "suffix" {
  description = "A few random letters/digits so storage, ACR and Key Vault names are globally unique."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "eastus2"
}

variable "image_tag" {
  description = "Tag of the recon-api image in ACR (the CI pipeline passes the git commit SHA)."
  type        = string
  default     = "latest"
}

variable "catalog" {
  description = "Unity Catalog catalog that holds the reconciliation tables."
  type        = string
  default     = "recon"
}

variable "databricks_sql_http_path" {
  description = "HTTP path of the Databricks SQL warehouse the assistant reads from (from the warehouse's Connection details)."
  type        = string
  default     = ""
}

variable "api_client_id" {
  description = "Application (client) ID of the Entra ID app registration that protects the API."
  type        = string
  default     = ""
}

variable "claude_model" {
  description = "Claude model the assistant uses."
  type        = string
  default     = "claude-opus-5-5"
}
