output "asg_ids" {
  value = {
        for id in keys(var.asg) : id => azurerm_subnet.subnet[id].id
    }
    description = "Lists the ID's of the asg"
}