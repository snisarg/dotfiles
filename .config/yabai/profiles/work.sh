#!/usr/bin/env sh
# shellcheck disable=SC2034 # routing_ready is consumed by the sourcing .yabairc

# Work laptop Space bindings. The original first desktop has no UUID on this
# machine, so identify it by its stable id. The remaining UUIDs survive Mission
# Control reordering but must be updated if a Space is deleted and recreated.
bind_profile_spaces() {
    label_space_by_id trading 1 || routing_ready=false
    label_space_by_uuid work-comms B875ECBD-5773-4F0F-B764-4066CA1414C8 || routing_ready=false
    label_space_by_uuid coding 32E6B421-90C8-490F-BE9A-0BB15071A0C1 || routing_ready=false
    label_space_by_uuid chrome-4 10E888AB-6EFF-4777-BB16-3FC664BEF94A || routing_ready=false
    label_space_by_uuid chrome-5 B1669F6C-08AF-423A-A05B-3B0E73EF454A || routing_ready=false
    label_space_by_uuid chrome-6 EFE7226A-95A1-459C-B39A-D86E60A94224 || routing_ready=false
    label_space_by_uuid chrome-8 DF16DA62-6B38-41B0-82A6-6D5485DC3532 || routing_ready=false
    label_space_by_uuid chatgpt 55AC6520-1076-4F83-B9A7-76F03A2DD733 || routing_ready=false
    label_space_by_uuid chrome-10 662AF40E-2F07-4436-A28A-127ED5F004C4 || routing_ready=false
    label_space_by_uuid mail E6CEEE9A-B0DB-4689-8AF1-1B7B55D1D29F || routing_ready=false
    label_space_by_uuid planning C4149482-42E1-4A24-B9BA-1616F685D716 || routing_ready=false
    label_space_by_uuid personal-comms 57B9647F-2E50-4BBC-9985-7910D79BD16B || routing_ready=false
}
