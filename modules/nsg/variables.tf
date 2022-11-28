# List of the ASG linked with the NSG
variable "asg" {
  type = map(any)
  default = {}
  description = "List of the ASG linked with the NSG"
}

# Location of the RG
variable "location" {
  type = string
  default = "westeurope"
  description = "Location of the NSG"
}

# Name of the NSG
variable "nsg_name" {
  type = string
  description = "The name of the NSG"
}

# List of the rules for the NSG
variable "nsg_rules" {
  type = map(any)
  description = "List of the rules for the NSG"
}

# Name of the RG
variable "resource_group_name" {
  type = string
  default = "rg_test"
  description = "Name of the RG"
}

# Add tags
variable "tags" {
  type = map(any)
  default = {
    "Test" = ""
  }
  description = "The tag values for the deployment"
}