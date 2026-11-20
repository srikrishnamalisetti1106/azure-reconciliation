# Azure infrastructure for the reconciliation platform.
#
#   ADLS Gen2         raw landing files + Delta tables (Unity Catalog external location)
#   Databricks        Premium workspace + access connector (managed identity for storage)
#   Key Vault         secrets (Anthropic key, Databricks token, Langfuse keys)
#   ACR               Docker images for the API
#   Container Apps    runs the API, scales to zero, pulls secrets from Key Vault
#   Log Analytics     logs for Container Apps
#
# Secrets are NOT created here (they would end up in Terraform state).
# Add them once with:  az keyvault secret set --vault-name <kv> --name anthropic-api-key --value <key>

terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.9"
    }
  }
  # Remote state, so CI and teammates share it. Create this storage account once by hand.
  # backend "azurerm" {
  #   resource_group_name  = "rg-tfstate"
  #   storage_account_name = "<tfstate account>"
  #   container_name       = "tfstate"
  #   key                  = "recon.tfstate"
  # }
}

provider "azurerm" {
  features {}
}

data "azurerm_client_config" "current" {}

locals {
  name = "${var.project}-${var.environment}"
  # Storage, ACR and Key Vault names: lowercase letters/digits only, globally unique.
  compact = lower(replace("${var.project}${var.environment}${var.suffix}", "-", ""))
  tags = {
    project     = var.project
    environment = var.environment
    managed_by  = "terraform"
  }
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# ---------------------------------------------------------------- storage (ADLS Gen2)
resource "azurerm_storage_account" "lake" {
  name                            = substr("st${local.compact}", 0, 24)
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = var.environment == "prod" ? "ZRS" : "LRS"
  account_kind                    = "StorageV2"
  is_hns_enabled                  = true # hierarchical namespace = ADLS Gen2
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = local.tags
}

resource "azurerm_storage_container" "landing" {
  name                  = "landing" # processor and ledger files arrive here
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "lakehouse" {
  name                  = "lakehouse" # Delta tables for bronze/silver/gold/reporting
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

# ---------------------------------------------------------------- Databricks
resource "azurerm_databricks_workspace" "main" {
  name                = "dbw-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "premium" # needed for Unity Catalog
  tags                = local.tags
}

# Managed identity Unity Catalog uses to read/write the lake (no storage keys).
resource "azurerm_databricks_access_connector" "uc" {
  name                = "dbac-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  identity {
    type = "SystemAssigned"
  }
  tags = local.tags
}

resource "azurerm_role_assignment" "uc_lake" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_databricks_access_connector.uc.identity[0].principal_id
}

# ---------------------------------------------------------------- Key Vault
resource "azurerm_key_vault" "main" {
  name                       = substr("kv${local.compact}", 0, 24)
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  enable_rbac_authorization  = true
  purge_protection_enabled   = var.environment == "prod"
  soft_delete_retention_days = 7
  tags                       = local.tags
}

# Whoever runs Terraform can manage secrets.
resource "azurerm_role_assignment" "deployer_kv" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# ---------------------------------------------------------------- container registry
resource "azurerm_container_registry" "main" {
  name                = substr("acr${local.compact}", 0, 50)
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Basic"
  admin_enabled       = false # pull with managed identity, not passwords
  tags                = local.tags
}

# ---------------------------------------------------------------- API identity
resource "azurerm_user_assigned_identity" "api" {
  name                = "id-${local.name}-api"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "api_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}

resource "azurerm_role_assignment" "api_kv_read" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}

# ---------------------------------------------------------------- Container Apps
resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.name}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags
}

resource "azurerm_container_app" "api" {
  name                         = "ca-${local.name}-api"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.api.id
  }

  # Secrets are references to Key Vault; the values never appear here or in state.
  secret {
    name                = "anthropic-api-key"
    key_vault_secret_id = "${azurerm_key_vault.main.vault_uri}secrets/anthropic-api-key"
    identity            = azurerm_user_assigned_identity.api.id
  }
  secret {
    name                = "databricks-token"
    key_vault_secret_id = "${azurerm_key_vault.main.vault_uri}secrets/databricks-token"
    identity            = azurerm_user_assigned_identity.api.id
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = 0 # scale to zero when idle
    max_replicas = 3

    container {
      name   = "api"
      image  = "${azurerm_container_registry.main.login_server}/recon-api:${var.image_tag}"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name        = "ANTHROPIC_API_KEY"
        secret_name = "anthropic-api-key"
      }
      env {
        name        = "DATABRICKS_TOKEN"
        secret_name = "databricks-token"
      }
      env {
        name  = "RECON_BACKEND"
        value = "databricks"
      }
      env {
        name  = "DATABRICKS_SERVER_HOSTNAME"
        value = azurerm_databricks_workspace.main.workspace_url
      }
      env {
        name  = "DATABRICKS_HTTP_PATH"
        value = var.databricks_sql_http_path
      }
      env {
        name  = "RECON_CATALOG"
        value = var.catalog
      }
      env {
        name  = "CLAUDE_MODEL"
        value = var.claude_model
      }
      env {
        name  = "AUTH_TENANT_ID"
        value = data.azurerm_client_config.current.tenant_id
      }
      env {
        name  = "AUTH_API_CLIENT_ID"
        value = var.api_client_id
      }

      liveness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/api/health"
      }
    }
  }

  depends_on = [
    azurerm_role_assignment.api_acr_pull,
    azurerm_role_assignment.api_kv_read,
  ]
}
