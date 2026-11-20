output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "storage_account" {
  value = azurerm_storage_account.lake.name
}

output "landing_url" {
  description = "Use as the Auto Loader / file-arrival location in databricks.yml."
  value       = "abfss://${azurerm_storage_container.landing.name}@${azurerm_storage_account.lake.name}.dfs.core.windows.net/"
}

output "lakehouse_url" {
  value = "abfss://${azurerm_storage_container.lakehouse.name}@${azurerm_storage_account.lake.name}.dfs.core.windows.net/"
}

output "databricks_workspace_url" {
  value = "https://${azurerm_databricks_workspace.main.workspace_url}"
}

output "databricks_access_connector_id" {
  description = "Use when creating the Unity Catalog storage credential."
  value       = azurerm_databricks_access_connector.uc.id
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "acr_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "container_app_name" {
  value = azurerm_container_app.api.name
}

output "api_url" {
  value = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}
