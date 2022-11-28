locals {
  asg_01 = {
    asg-Test1 = {
        name                = "asg-Test1"
        location            = azurerm_resource_group.rg.location
        resource_group_name = azurerm_resource_group.rg.name
    }
  }

  nsg_rules_01 = {
    rules1 = {
        access : "Allow"
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
        access : "Allow"
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