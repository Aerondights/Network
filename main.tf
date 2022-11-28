# Create RG
resource "azurerm_resource_group" "rg_test" {
  name = "rg_test"
  location = "westeurope"
}

module "nsg_01" {
    source = "./modules/nsg"
    nsg_name = "nsg01"
    nsg_rules = local.nsg_rules_01
}

module "nsg_01" {
    source = "./modules/nsg"
    nsg_name = "nsg02"
    nsg_rules = local.nsg_rules_012
}