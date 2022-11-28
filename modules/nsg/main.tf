# Create ASG from locals
resource "azurerm_application_security_group" "asg" {
  for_each            = var.asg
  name                = each.key
  resource_group_name = each.value.resource_group_name
  location            = each.value.location
}

# Create NSG from locals
resource "azurerm_network_security_group" "nsg" {
  name                = var.nsg_name
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location

  /*dynamic "security_rule" {
    for_each = var.nsg_rules
    content {
      name                       = security_rule.value["name"]
      priority                   = security_rule.value["priority"]
      direction                  = security_rule.value["direction"]
      access                     = security_rule.value["access"]
      protocol                   = security_rule.value["protocol"]
      source_port_range          = security_rule.value["source_port_range"]
      destination_port_range     = security_rule.value["destination_port_range"]
      source_address_prefix      = security_rule.value["source_address_prefix"]
      destination_address_prefix = security_rule.value["destination_address_prefix"]
    }
  }*/
}

#Create a resource group with a default name and location
resource "azurerm_resource_group" "rg" {
  name                = var.resource_group_name
  location            = var.location
}

# Createrules for the NSG from locals
resource "azurerm_network_security_rule" "rules" {
  for_each                              = var.nsg_rules
  name                                  = each.key
  direction                             = each.value.direction
  access                                = each.value.access
  priority                              = each.value.priority
  protocol                              = each.value.protocol
  source_port_range                     = each.value.source_port_range
  destination_port_range                = each.value.destination_port_range
  source_address_prefix                 = each.value.source_address_prefix
  destination_address_prefix            = each.value.destination_address_prefix
  source_application_security_group_ids = each.value.source_application_security_group_ids
  resource_group_name                   = azurerm_resource_group.rg.name
  network_security_group_name           = azurerm_network_security_group.nsg.name

  tags = var.tags
}