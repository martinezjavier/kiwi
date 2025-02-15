#!/bin/bash
type getarg >/dev/null 2>&1 || . /lib/dracut-lib.sh
type lookup_disk_device_from_root >/dev/null 2>&1 || . /lib/kiwi-lib.sh
type create_parted_partitions >/dev/null 2>&1 || . /lib/kiwi-partitions-lib.sh
type resize_filesystem >/dev/null 2>&1 || . /lib/kiwi-filesystem-lib.sh
type activate_volume_group >/dev/null 2>&1 || . /lib/kiwi-lvm-lib.sh
type activate_mdraid >/dev/null 2>&1 || . /lib/kiwi-mdraid-lib.sh
type luks_system >/dev/null 2>&1 || . /lib/kiwi-luks-lib.sh

#======================================
# Functions
#--------------------------------------
function initialize {
    declare root=${root}
    local profile=/.profile
    local partition_ids=/config.partids

    test -f ${profile} || \
        die "No profile setup found"
    test -f ${partition_ids} || \
        die "No partition id setup found"

    import_file ${profile}
    import_file ${partition_ids}

    disk=$(lookup_disk_device_from_root)
    export disk

    root_device=${root#block:}
    export root_device

    swapsize=$(get_requested_swap_size)
    export swapsize

    disk_free_mbytes=$((
        $(get_free_disk_bytes "${disk}") / 1048576
    ))
    export disk_free_mbytes

    disk_root_mbytes=$((
        $(get_block_device_kbsize "${root_device}") / 1024
    ))
    export disk_root_mbytes
}

function get_requested_swap_size {
    declare kiwi_oemswapMB=${kiwi_oemswapMB}
    declare kiwi_oemswap=${kiwi_oemswap}
    local swapsize
    if [ -n "${kiwi_oemswapMB}" ];then
        # swap size configured by kiwi description
        swapsize=${kiwi_oemswapMB}
    else
        # default swap size is twice times ramsize
        swapsize=$((
            $(grep MemTotal: /proc/meminfo | tr -dc '0-9') * 2 / 1024
        ))
    fi
    if [ ! "${kiwi_oemswap}" = "true" ];then
        # no swap wanted by kiwi description
        swapsize=0
    fi
    echo ${swapsize}
}

function deactivate_device_mappings {
    if lvm_system;then
        deactivate_volume_group
    fi
    if mdraid_system;then
        deactivate_mdraid
    fi
    if luks_system "${disk}";then
        deactivate_luks
    fi
}

function finalize_disk_repart {
    declare kiwi_RootPart=${kiwi_RootPart}
    finalize_partition_table "${disk}"
    set_root_map \
        "$(get_partition_node_name "${disk}" "${kiwi_RootPart}")"
}

function repart_standard_disk {
    # """
    # repartition disk with read/write root filesystem
    # Image partition table layout is:
    # =====================================
    # pX:   [ boot ]
    # pX+1: ( root )  [+luks +raid]
    # -------------------------------------
    # """
    declare kiwi_oemrootMB=${kiwi_oemrootMB}
    declare kiwi_RootPart=${kiwi_RootPart}
    if [ -z "${kiwi_oemrootMB}" ];then
        local disk_have_root_system_mbytes=$((
            disk_root_mbytes + disk_free_mbytes - swapsize
        ))
        local min_additional_mbytes=${swapsize}
    else
        local disk_have_root_system_mbytes=${kiwi_oemrootMB}
        local min_additional_mbytes=$((
            swapsize + kiwi_oemrootMB - disk_root_mbytes
        ))
    fi
    if [ "${min_additional_mbytes}" -lt 5 ];then
        min_additional_mbytes=5
    fi
    local new_parts=0
    if [ "${kiwi_oemswap}" = "true" ];then
        new_parts=$((new_parts + 1))
    fi
    # check if we can repart this disk
    if ! check_repart_possible \
        ${disk_root_mbytes} ${disk_free_mbytes} ${min_additional_mbytes}
    then
        return 1
    fi
    # deactivate all active device mappings
    deactivate_device_mappings
    # repart root partition
    local command_query
    local root_part_size=+${disk_have_root_system_mbytes}M
    if [ -z "${kiwi_oemrootMB}" ] && [ ${new_parts} -eq 0 ];then
        # no new parts and no rootsize limit, use rest disk space
        root_part_size=.
    fi
    command_query="
        d ${kiwi_RootPart}
        n p:lxroot ${kiwi_RootPart} . ${root_part_size}
    "
    create_parted_partitions \
        "${disk}" "${command_query}"
    # add swap partition
    create_swap_partition "$new_parts"
    # finalize table changes
    finalize_disk_repart
}

function repart_lvm_disk {
    # """
    # repartition disk if LVM partition plus boot partition
    # is used. Initial partition table layout is:
    # =====================================
    # pX:   ( boot )
    # pX+1: ( LVM  )  [+luks +raid]
    # -------------------------------------
    # """
    declare kiwi_oemrootMB=${kiwi_oemrootMB}
    declare kiwi_RootPart=${kiwi_RootPart}
    if [ -z "${kiwi_oemrootMB}" ];then
        local disk_have_root_system_mbytes=$((
            disk_root_mbytes + disk_free_mbytes
        ))
        local min_additional_mbytes=${swapsize}
    else
        local disk_have_root_system_mbytes=$((
            kiwi_oemrootMB + swapsize
        ))
        local min_additional_mbytes=$((
            swapsize + kiwi_oemrootMB - disk_root_mbytes
        ))
    fi
    if [ "${min_additional_mbytes}" -lt 5 ];then
        min_additional_mbytes=5
    fi
    # check if we can repart this disk
    if ! check_repart_possible \
        ${disk_root_mbytes} ${disk_free_mbytes} ${min_additional_mbytes}
    then
        return 1
    fi
    # deactivate all active device mappings
    deactivate_device_mappings
    # create lvm.conf appropriate for resize
    setup_lvm_config
    # repart lvm partition
    local command_query
    local lvm_part_size=+${disk_have_root_system_mbytes}M
    if [ -z "${kiwi_oemrootMB}" ];then
        # no rootsize limit, use rest disk space
        lvm_part_size=.
    fi
    command_query="
        d ${kiwi_RootPart}
        n p:lxlvm ${kiwi_RootPart} . ${lvm_part_size}
        t ${kiwi_RootPart} 8e
    "
    create_parted_partitions \
        "${disk}" "${command_query}"
    # finalize table changes
    finalize_disk_repart
}

function create_swap_volume {
    if [ "${swapsize}" -gt "0" ];then
        if create_volume "LVSwap" "${swapsize}";then
            set_swap_map "$(get_volume_path_for_volume "LVSwap")"
        fi
    fi
}

function create_swap_partition {
    declare kiwi_oemrootMB=${kiwi_oemrootMB}
    declare kiwi_RootPart=${kiwi_RootPart}
    local new_parts=$1
    if [ "${swapsize}" -gt "0" ];then
        local swap_part=$((kiwi_RootPart + 1))
        local swap_part_size=+${swapsize}M
        if [ -z "${kiwi_oemrootMB}" ] && [ "${new_parts}" -eq "1" ];then
            # exactly one new part and no rootsize limit, use rest disk space
            swap_part_size=.
        fi
        command_query="
            n p:lxswap ${swap_part} . ${swap_part_size}
            t ${swap_part} 82
        "
        create_parted_partitions \
            "${disk}" "${command_query}"
        set_swap_map \
            "$(get_persistent_device_from_unix_node \
                "$(get_partition_node_name "${disk}" "${swap_part}")" "by-id"
            )"
    fi
}

function check_repart_possible {
    declare kiwi_oemrootMB=${kiwi_oemrootMB}
    local disk_root_mbytes=$1
    local disk_free_mbytes=$2
    local min_additional_mbytes=$3
    if [ -n "${kiwi_oemrootMB}" ];then
        if [ "${kiwi_oemrootMB}" -lt "${disk_root_mbytes}" ];then
            # specified oem-systemsize is smaller than root partition
            warn "Requested OEM systemsize is smaller than root partition:"
            warn "Disk won't be re-partitioned !"
            echo
            warn "Current Root partition: ${disk_root_mbytes} MB"
            warn "==> Requested size: ${kiwi_oemrootMB} MB"
            return 1
        fi
    fi
    if [ "${min_additional_mbytes}" -gt "${disk_free_mbytes}" ];then
        # Requested sizes for root and swap exceeds free space on disk
        local requested_size
        if [ -n "${kiwi_oemrootMB}" ];then
            requested_size="root:($((kiwi_oemrootMB - disk_root_mbytes)) MB)"
        else
            requested_size="root:(keep)"
        fi
        if [ ${swapsize} -gt 0 ];then
            requested_size="${requested_size}, swap:(${swapsize} MB)"
        fi
        warn "Requested OEM systemsize exceeds free space on the disk:"
        warn "Disk won't be re-partitioned !"
        echo
        warn "Requested size(s): ${requested_size}"
        warn "==> Free Space on disk: ${disk_free_mbytes} MB"
        return 1
    fi
    return 0
}

function mask_fsck_root_service {
    info "disable systemd-fsck-root.service"
    systemctl mask systemd-fsck-root.service
}

function unmask_fsck_root_service {
    info "enabling systemd-fsck-root.service"
    systemctl unmask systemd-fsck-root.service
}

#======================================
# Perform repart/resize operations
#--------------------------------------
PATH=/usr/sbin:/usr/bin:/sbin:/bin

setup_debug

# initialize for disk repartition
initialize

if ! disk_has_unallocated_space "${disk}";then
    # already resized or disk has not received any geometry change
    return
fi

# when repartitioning disks, parted and friends might trigger re-reads of
# the partition table, in turn triggering systemd-fsck-root.service
# repeatedly via udev events, which finally can cause booting to fail with
# * start request repeated too quickly for systemd-fsck-root.service
# * Failed to start File System Check on /dev/disk/by-uuid...
# * Dependency failed for /sysroot.
# To avoid this, disable the root fsck (is finished at this point anyway
# *and* the filesystem is brand new ;) by masking it.
# "systemctl disable" does not work here, because it is event driven
# More details: https://github.com/SUSE/kiwi/issues/1034

# make sure we unmask the fsck service
trap unmask_fsck_root_service EXIT

mask_fsck_root_service

# prepare disk for repartition
if [ "$(get_partition_table_type "${disk}")" = 'gpt' ];then
    relocate_gpt_at_end_of_disk "${disk}"
fi

# wait for the root device to appear
wait_for_storage_device "${root_device}"

# resize disk partition table
if lvm_system;then
    repart_lvm_disk || return
else
    repart_standard_disk || return
fi

# resize luks if present
if luks_system "${disk}";then
    activate_luks "$(get_root_map)"
    resize_luks
fi

# resize raid if present
if mdraid_system;then
    activate_mdraid
    resize_mdraid
fi

# resize volumes and filesystems
if lvm_system; then
    resize_pyhiscal_volumes
    activate_volume_group
    create_swap_volume
    resize_lvm_volumes_and_filesystems
else
    resize_filesystem "$(get_root_map)"
fi

# create swap space
create_swap "$(get_swap_map)"
