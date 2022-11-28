locals {
  asg_02 = {
    asg-Test2 = {
        name                = "asg-Test2"
        location            = azurerm_resource_group.rg.location
        resource_group_name = azurerm_resource_group.rg.name
    }
  }

  nsg_rules_02 = {
    rules1 = {
        access : "Deny"
        description : "test"
        destination_address_prefix: "*"
        destination_port_range: "443"
        direction : "Inbound"
        name : "rules1"
        priority : "100"
        protocol : "Tcp"
        source_address_prefix : ""
        source_port_range : "*"
        source_application_security_group_ids : [""]
    }

    rules2 = {
        access : "Deny"
        description : "test"
        destination_address_prefix: "*"
        destination_port_range: "443"
        direction : "Outbound"
        name : "rules2"
        priority : "100"
        protocol : "Tcp"
        source_address_prefix : ""
        source_port_range : "*"
        source_application_security_group_ids : [module.nsg01.asg_ids["asg-Test1"]]
    }
  }
}