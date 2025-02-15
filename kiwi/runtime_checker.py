# Copyright (c) 2015 SUSE Linux GmbH.  All rights reserved.
#
# This file is part of kiwi.
#
# kiwi is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# kiwi is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with kiwi.  If not, see <http://www.gnu.org/licenses/>
#
import os
import platform
from textwrap import dedent

# project
from .xml_description import XMLDescription
from .xml_state import XMLState
from .system.uri import Uri
from .defaults import Defaults
from .path import Path
from .utils.command_capabilities import CommandCapabilities
from .runtime_config import RuntimeConfig
from .exceptions import (
    KiwiRuntimeError
)


class RuntimeChecker:
    """
    **Implements build consistency checks at runtime**

    The schema of an image description covers structure and syntax of
    the provided data. The RuntimeChecker provides methods to perform
    further semantic checks which allows to recognize potential build
    or boot problems early.

    * :param object xml_state: Instance of XMLState
    """
    def __init__(self, xml_state):
        self.xml_state = xml_state

    def check_repositories_configured(self):
        """
        Verify that that there are repositories configured
        """
        if not self.xml_state.get_repository_sections():
            raise KiwiRuntimeError(
                'No repositories configured'
            )

    def check_image_include_repos_publicly_resolvable(self):
        """
        Verify that all repos marked with the imageinclude attribute
        can be resolved into a http based web URL
        """
        message = dedent('''\n
            The use of imageinclude="true" in the repository definition
            for the Repository: {0}
            requires the repository to by publicly available. The source
            locator of the repository however indicates it is private to
            your local system. Therefore it can't be included into the
            system image repository configuration. Please define a publicly
            available repository in your image XML description.
        ''')

        repository_sections = self.xml_state.get_repository_sections()
        for xml_repo in repository_sections:
            repo_marked_for_image_include = xml_repo.get_imageinclude()

            if repo_marked_for_image_include:
                repo_source = xml_repo.get_source().get_path()
                repo_type = xml_repo.get_type()
                uri = Uri(repo_source, repo_type)
                if not uri.is_public():
                    raise KiwiRuntimeError(
                        message.format(repo_source)
                    )

    def check_target_directory_not_in_shared_cache(self, target_dir):
        """
        The target directory must be outside of the kiwi shared cache
        directory in order to avoid busy mounts because kiwi bind mounts
        the cache directory into the image root tree to access host
        caching information

        :param string target_dir: path name
        """
        message = dedent('''\n
            Target directory %s conflicts with kiwi's shared cache
            directory %s. This is going to create a busy loop mount.
            Please choose another target directory.
        ''')

        shared_cache_location = Defaults.get_shared_cache_location()

        target_dir_stack = os.path.abspath(
            os.path.normpath(target_dir)
        ).replace(os.sep + os.sep, os.sep).split(os.sep)
        if target_dir_stack[1:4] == shared_cache_location.split(os.sep):
            raise KiwiRuntimeError(
                message % (target_dir, shared_cache_location)
            )

    def check_volume_label_used_with_lvm(self):
        """
        The optional volume label in a systemdisk setup is only
        effective if the LVM, logical volume manager system is
        used. In any other case where the filesystem itself offers
        volume management capabilities there are no extra filesystem
        labels which can be applied per volume
        """
        message = dedent('''\n
            Custom volume label setup used without LVM

            The optional volume label in a systemdisk setup is only
            effective if the LVM, logical volume manager system is
            used. Your setup uses the {0} filesystem which itself
            offers volume management capabilities. Extra filesystem
            labels cannot be applied in this case.

            If you want to force LVM over the {0} volume management
            system you can do so by specifying the following in
            your KIWI XML description:

            <systemdisk ... preferlvm="true">
                <volume .../>
            </systemdisk>
        ''')
        volume_management = self.xml_state.get_volume_management()
        if volume_management != 'lvm':
            for volume in self.xml_state.get_volumes():
                if volume.label:
                    raise KiwiRuntimeError(
                        message.format(volume_management)
                    )

    def check_volume_setup_defines_multiple_fullsize_volumes(self):
        """
        The volume size specification 'all' makes this volume to
        take the rest space available on the system. It's only
        allowed to specify one all size volume
        """
        message = dedent('''\n
            Multiple all size volumes found but only one is allowed

            The volume size specification 'all' makes this volume to
            take the rest space available on the system. It's only
            allowed to specify one all size volume
        ''')
        systemdisk_section = self.xml_state.get_build_type_system_disk_section()
        if systemdisk_section:
            all_size_volume_count = 0
            volumes = systemdisk_section.get_volume() or []
            for volume in volumes:
                size = volume.get_size() or volume.get_freespace()
                if size and 'all' in size:
                    all_size_volume_count += 1
            if all_size_volume_count > 1:
                raise KiwiRuntimeError(message)

    def check_volume_setup_has_no_root_definition(self):
        """
        The root volume in a systemdisk setup is handled in a special
        way. It is not allowed to setup a custom name or mountpoint for
        the root volume. Therefore the size of the root volume can be
        setup via the @root volume name. This check looks up the volume
        setup and searches if there is a configuration for the '/'
        mountpoint which would cause the image build to fail
        """
        message = dedent('''\n
            Volume setup for "/" found. The size of the root volume
            must be specified via the @root volume name like the
            following example shows:

            <volume name="@root" size="42G"/>

            A custom name or mountpoint for the root volume is not
            allowed.
        ''')
        for volume in self.xml_state.get_volumes():
            if volume.mountpoint == '/':
                raise KiwiRuntimeError(message)

    def check_container_tool_chain_installed(self):
        """
        When creating container images the specific tools are used in order
        to import and export OCI or Docker compatible images. This check
        searches for those tools to be installed in the build system and
        fails if it can't find them
        """
        message_tool_not_found = dedent('''\n
            Required tool {name} not found in caller environment

            Creation of OCI or Docker images requires the tools {name} and
            skopeo to be installed on the build system. For SUSE based systems
            you can find the tools at:

            http://download.opensuse.org/repositories/Virtualization:/containers
        ''')
        message_version_unsupported = dedent('''\n
            {name} tool found with unknown version
        ''')
        message_unknown_tool = dedent('''\n
            Unknown tool: {0}.

            Please configure KIWI with an appropriate value (umoci or buildah).
            Consider this runtime configuration file syntax (/etc/kiwi.yml):

            oci:
                - archive_tool: umoci | buildah
        ''')

        expected_version = (0, 1, 0)

        if self.xml_state.get_build_type_name() in ['docker', 'oci']:
            runtime_config = RuntimeConfig()
            tool_name = runtime_config.get_oci_archive_tool()
            if tool_name == 'buildah':
                oci_tools = ['buildah', 'skopeo']
            elif tool_name == 'umoci':
                oci_tools = ['umoci', 'skopeo']
            else:
                raise KiwiRuntimeError(message_unknown_tool.format(tool_name))
            for tool in oci_tools:
                if not Path.which(filename=tool, access_mode=os.X_OK):
                    raise KiwiRuntimeError(
                        message_tool_not_found.format(name=tool)
                    )
                elif not CommandCapabilities.check_version(
                    tool, expected_version, raise_on_error=False
                ):
                    raise KiwiRuntimeError(
                        message_version_unsupported.format(name=tool)
                    )
            self._check_multitag_support()

    def _check_multitag_support(self):
        message = dedent('''\n
            Using additionaltags attribute requires skopeo tool to be
            capable to handle it, it must include the '--additional-tag'
            option for copy subcommand (check it running 'skopeo copy
            --help').\n
            It is known to be present since v0.1.30
        ''')
        if 'additional_tags' in self.xml_state.get_container_config():
            if not CommandCapabilities.has_option_in_help(
                'skopeo', '--additional-tag', ['copy', '--help'],
                raise_on_error=False
            ):
                raise KiwiRuntimeError(message)

    def check_boot_description_exists(self):
        """
        If a kiwi initrd is used, a lookup to the specified boot
        description is done and fails early if it does not exist
        """
        message_no_boot_reference = dedent('''\n
            Boot description missing for '{0}' type

            The selected '{1}' initrd_system requires a boot description
            reference. Please update your type setup as follows

            <type image="{0}" boot="{0}boot/..."/>

            A collection of custom boot descriptions can be found
            in the kiwi-boot-descriptions package
        ''')
        message_boot_description_not_found = dedent('''\n
            Boot description '{0}' not found

            The selected boot description could not be found on
            the build host. A collection of custom boot descriptions
            can be found in the kiwi-boot-descriptions package
        ''')
        image_types_supporting_custom_boot_description = ['oem', 'pxe']
        build_type = self.xml_state.get_build_type_name()
        initrd_system = self.xml_state.get_initrd_system()
        if initrd_system == 'kiwi' and \
           build_type in image_types_supporting_custom_boot_description:

            boot_image_reference = self.xml_state.build_type.get_boot()
            if not boot_image_reference:
                raise KiwiRuntimeError(
                    message_no_boot_reference.format(build_type, initrd_system)
                )

            if not boot_image_reference[0] == os.sep:
                boot_image_reference = os.sep.join(
                    [
                        Defaults.get_boot_image_description_path(),
                        boot_image_reference
                    ]
                )
            if not os.path.exists(boot_image_reference):
                raise KiwiRuntimeError(
                    message_boot_description_not_found.format(
                        boot_image_reference
                    )
                )

    def check_consistent_kernel_in_boot_and_system_image(self):
        """
        If a kiwi initrd is used, the kernel used to build the kiwi
        initrd and the kernel used in the system image must be the
        same in order to avoid an inconsistent boot setup
        """
        message = dedent('''\n
            Possible kernel mismatch between kiwi initrd and system image

            The selected '{0}' boot image kernel is '{1}'. However this
            kernel package was not explicitly listed in the package list
            of the system image. Please fixup your system image
            description:

            1) Add <package name="{1}"/> to your system XML description

            2) Inherit kernel from system description to initrd via
               the custom kernel profile:

               <type ... bootkernel="custom" .../>

               <packages type="image"/>
                   <package name="desired-kernel" bootinclude="true"/>
               </packages>
        ''')
        boot_image_reference = self.xml_state.build_type.get_boot()
        boot_kernel_package_name = None
        if boot_image_reference:
            if not boot_image_reference[0] == '/':
                boot_image_reference = os.sep.join(
                    [
                        Defaults.get_boot_image_description_path(),
                        boot_image_reference
                    ]
                )
            boot_config_file = os.sep.join(
                [boot_image_reference, 'config.xml']
            )
            if os.path.exists(boot_config_file):
                boot_description = XMLDescription(
                    description=boot_config_file,
                    derived_from=self.xml_state.xml_data.description_dir
                )
                boot_kernel_profile = \
                    self.xml_state.build_type.get_bootkernel()
                if not boot_kernel_profile:
                    boot_kernel_profile = 'std'
                boot_xml_state = XMLState(
                    boot_description.load(), [boot_kernel_profile]
                )
                kernel_package_sections = []
                for packages_section in boot_xml_state.xml_data.get_packages():
                    # lookup package sections matching kernel profile in kiwi
                    # boot description. By definition this must be a packages
                    # section with a single profile name whereas the default
                    # profile name is 'std'. The section itself must contain
                    # one matching kernel package name for the desired
                    # architecture
                    if packages_section.get_profiles() == boot_kernel_profile:
                        for package in packages_section.get_package():
                            kernel_package_sections.append(package)

                for package in kernel_package_sections:
                    if boot_xml_state.package_matches_host_architecture(
                            package
                    ):
                        boot_kernel_package_name = package.get_name()

        if boot_kernel_package_name:
            # A kernel package name was found in the kiwi boot image
            # description. Let's check if this kernel is also used
            # in the system image
            image_package_names = self.xml_state.get_system_packages()
            if boot_kernel_package_name not in image_package_names:
                raise KiwiRuntimeError(
                    message.format(
                        self.xml_state.build_type.get_boot(),
                        boot_kernel_package_name
                    )
                )

    def check_dracut_module_for_oem_install_in_package_list(self):
        """
        OEM images if configured to use dracut as initrd system
        and configured with one of the installiso, installstick
        or installpxe attributes requires the KIWI provided
        dracut-kiwi-oem-dump module to be installed at the time
        dracut is called. Thus this runtime check examines if the
        required package is part of the package list in the
        image description.
        """
        message = dedent('''\n
            Required dracut module package missing in package list

            The package '{0}' is required to build an installation
            image for the selected oem image type. Please add the
            following in your <packages type="image"> section to
            your system XML description:

            <package name="{0}"/>
        ''')
        required_dracut_package = 'dracut-kiwi-oem-dump'
        initrd_system = self.xml_state.get_initrd_system()
        build_type = self.xml_state.get_build_type_name()
        if build_type == 'oem' and initrd_system == 'dracut':
            install_iso = self.xml_state.build_type.get_installiso()
            install_stick = self.xml_state.build_type.get_installstick()
            install_pxe = self.xml_state.build_type.get_installpxe()
            if install_iso or install_stick or install_pxe:
                package_names = \
                    self.xml_state.get_bootstrap_packages() + \
                    self.xml_state.get_system_packages()
                if required_dracut_package not in package_names:
                    raise KiwiRuntimeError(
                        message.format(required_dracut_package)
                    )

    def check_architecture_supports_iso_firmware_setup(self):
        """
        For creating ISO images a different bootloader setup is
        performed depending on the configured firmware. If the
        firmware is set to bios, isolinux is used and that limits
        the architecture to x86 only. In any other case the appliance
        configured bootloader is used. This check examines if the
        host architecture is supported with the configured firmware
        on request of an ISO image.
        """
        message = dedent('''\n
            Unsupported firmware setup: {0} on {1} architecture

            The selected firmware limits the creation of images to
            the x86 platform. For the detected build host architecture
            and the request to build a live or install ISO media
            the EFI firmware must be used:

            <type ... firmware="efi"/>
        ''')
        arch = platform.machine()
        build_type = self.xml_state.get_build_type_name()
        firmware = self.xml_state.build_type.get_firmware() or \
            Defaults.get_default_firmware(arch)
        if firmware == 'bios' and not Defaults.is_x86_arch(arch):
            iso_build_requested = False
            if build_type == 'iso':
                iso_build_requested = True
            elif build_type == 'oem':
                install_iso = self.xml_state.build_type.get_installiso()
                install_stick = self.xml_state.build_type.get_installstick()
                if install_iso or install_stick:
                    iso_build_requested = True
            if iso_build_requested:
                raise KiwiRuntimeError(
                    message.format(firmware, arch)
                )

    def check_dracut_module_for_disk_oem_in_package_list(self):
        """
        OEM images if configured to use dracut as initrd system
        requires the KIWI provided dracut-kiwi-oem-repart module
        to be installed at the time dracut is called. Thus this
        runtime check examines if the required package is part of
        the package list in the image description.
        """
        message = dedent('''\n
            Required dracut module package missing in package list

            The package '{0}' is required for the selected
            oem image type. Please add the following in your
            <packages type="image"> section to your system XML
            description:

            <package name="{0}"/>
        ''')
        required_dracut_package = 'dracut-kiwi-oem-repart'
        initrd_system = self.xml_state.get_initrd_system()
        build_type = self.xml_state.get_build_type_name()
        if build_type == 'oem' and initrd_system == 'dracut':
            package_names = \
                self.xml_state.get_bootstrap_packages() + \
                self.xml_state.get_system_packages()
            if required_dracut_package not in package_names:
                raise KiwiRuntimeError(
                    message.format(required_dracut_package)
                )

    def check_dracut_module_for_live_iso_in_package_list(self):
        """
        Live ISO images uses a dracut initrd to boot and requires
        the KIWI provided kiwi-live dracut module to be installed
        at the time dracut is called. Thus this runtime check
        examines if the required package is part of the package
        list in the image description.
        """
        message = dedent('''\n
            Required dracut module package missing in package list

            The package '{0}' is required for the selected
            live iso image type. Please add the following in your
            <packages type="image"> section to your system XML
            description:

            <package name="{0}"/>
        ''')
        required_dracut_package = 'dracut-kiwi-live'
        type_name = self.xml_state.get_build_type_name()
        type_flag = self.xml_state.build_type.get_flags()
        if type_name == 'iso' and type_flag != 'dmsquash':
            package_names = \
                self.xml_state.get_bootstrap_packages() + \
                self.xml_state.get_system_packages()
            if required_dracut_package not in package_names:
                raise KiwiRuntimeError(
                    message.format(required_dracut_package)
                )

    def check_dracut_module_for_disk_overlay_in_package_list(self):
        """
        Disk images configured to use a root filesystem overlay
        requires the KIWI provided kiwi-overlay dracut module to
        be installed at the time dracut is called. Thus this
        runtime check examines if the required package is part of
        the package list in the image description.
        """
        message = dedent('''\n
            Required dracut module package missing in package list

            The package '{0}' is required for the selected
            overlayroot activated image type. Please add the
            following in your <packages type="image"> section to
            your system XML description:

            <package name="{0}"/>
        ''')
        required_dracut_package = 'dracut-kiwi-overlay'
        if self.xml_state.build_type.get_overlayroot():
            package_names = \
                self.xml_state.get_bootstrap_packages() + \
                self.xml_state.get_system_packages()
            if required_dracut_package not in package_names:
                raise KiwiRuntimeError(
                    message.format(required_dracut_package)
                )

    def check_efi_mode_for_disk_overlay_correctly_setup(self):
        """
        Disk images configured to use a root filesystem overlay
        only supports the standard EFI mode and not secure boot.
        That's because the shim setup performs changes to the
        root filesystem which can not be applied during the
        bootloader setup at build time because at that point
        the root filesystem is a read-only squashfs source.
        """
        message = dedent('''\n
            Secure Boot not supported with overlay disk image

            Disk images configured to use a root filesystem overlay
            only supports the standard EFI mode and not secure boot.
            That's because the shim setup performs changes to the
            root filesystem which can not be applied during the
            bootloader setup at build time because at that point
            the root filesystem is a read-only squashfs source

            Thus please change the firmware attribute in the <type>
            section of the system XML description as follows:

            <type ... firmware="efi"/>
        ''')
        overlayroot = self.xml_state.build_type.get_overlayroot()
        firmware = self.xml_state.build_type.get_firmware()
        if overlayroot and firmware == 'uefi':
            raise KiwiRuntimeError(message)

    def check_xen_uniquely_setup_as_server_or_guest(self):
        """
        If the image is classified to be used as Xen image, it can
        be either a Xen Server(dom0) or a Xen guest. The image
        configuration is checked if the information uniquely identifies
        the image as such
        """
        xen_message = dedent('''\n
            Inconsistent Xen setup found:

            The use of the 'xen_server' or 'xen_loader' attributes indicates
            the target system for this image is Xen. However the image
            specifies both attributes at the same time which classifies
            the image to be both, a Xen Server(dom0) and a Xen guest at
            the same time, which is not supported.

            Please cleanup your image description. Setup only one
            of 'xen_server' or 'xen_loader'.
        ''')
        ec2_message = dedent('''\n
            Inconsistent Amazon EC2 setup found:

            The firmware setup indicates the target system for this image
            is Amazon EC2, which uses a Xen based virtualisation technology.
            Therefore the image must be classified as a Xen guest and can
            not be a Xen server as indicated by the 'xen_server' attribute

            Please cleanup your image description. Delete the 'xen_server'
            attribute for images used with Amazon EC2.
        ''')
        if self.xml_state.is_xen_server() and self.xml_state.is_xen_guest():
            firmware = self.xml_state.build_type.get_firmware()
            ec2_firmware_names = Defaults.get_ec2_capable_firmware_names()
            if firmware and firmware in ec2_firmware_names:
                raise KiwiRuntimeError(ec2_message)
            else:
                raise KiwiRuntimeError(xen_message)

    def check_mediacheck_installed(self):
        """
        If the image description enables the mediacheck attribute
        the required tools to run this check must be installed
        on the image build host
        """
        message_tool_not_found = dedent('''\n
            Required tool {name} not found in caller environment

            The attribute 'mediacheck' is set to 'true' which requires
            the above tool to be installed on the build system
        ''')
        if self.xml_state.build_type.get_mediacheck() is True:
            tool = 'tagmedia'
            if not Path.which(filename=tool, access_mode=os.X_OK):
                raise KiwiRuntimeError(
                    message_tool_not_found.format(name=tool)
                )

    def check_minimal_required_preferences(self):
        """
        Kiwi requires some of the elements of the `preferences` element
        to be present at least in one of the preferences section. This
        runtime check validates <version> and <packagemanager> are
        provided.
        """
        message_missing_package_manager = dedent('''\n
            No package manager is defined in any of the <preferences>
            sections. Please add

                <packagemanager>desired_package_manager<packagemanager/>

            inside the <preferences> section.
        ''')

        message_missing_version = dedent('''\n
            No version is defined in any of the <preferences>
            sections. Please add

                <version>image_version<version/>

            inside the <preferences> section.
        ''')

        if not self.xml_state.get_package_manager():
            raise KiwiRuntimeError(message_missing_package_manager)

        if not self.xml_state.get_image_version():
            raise KiwiRuntimeError(message_missing_version)
