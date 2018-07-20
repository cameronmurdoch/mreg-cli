import traceback
import inspect
import re
import types
import typing
import requests
import ipaddress

from util import *
from config import *
from history import history
from log import *

try:
    conf = cli_config(required_fields=("server_ip", "server_port"))
except Exception as e:
    print("commands.py: cli_config:", e)
    traceback.print_exc()
    sys.exit(1)


################################################################################
#                                                                              #
#   Base class of CLI commands                                                 #
#                                                                              #
################################################################################

class CommandBase():
    """
    Base class of all commands for the mreg client. It provide functions which uses insight to
    auto-generate documentation and cli-info.

    To add a new option to the command create a opt_<option-name> method which takes a list of
    arguments as input.
    """

    def __init__(self):
        pass

    @staticmethod
    def _is_option(value):
        """Identify an option method"""
        if isinstance(value, types.MethodType):
            if re.match("^opt_.*$", value.__name__):
                return True
        return False

    def _option_methods(self) -> typing.List[typing.Tuple[str, typing.Callable]]:
        """Return all option methods of self"""
        # getmembers returns a list of tuples with: (<method name>, <method object>)
        return inspect.getmembers(self, predicate=self._is_option)

    def help(self) -> str:
        """Generate a help message of the command (self) and all its options"""
        help_str = "{}\n\nOptions:\n".format(inspect.getdoc(self))
        for method in self._option_methods():
            assert isinstance(method[1], types.MethodType)
            for line in inspect.getdoc(method[1]).splitlines(keepends=False):
                help_str += "   {}\n".format(line)
        return help_str

    def options(self) -> typing.List[str]:
        """Returns all options of this command, identified by function prefix "opt_\""""
        options = []
        for method in self._option_methods():
            options.append(method[0].split('_', maxsplit=1)[1])
        return options

    def opt_help(self, opt: str) -> None:
        """
        help <option>
            Return the documentation for the option.
        """
        for method in self._option_methods():
            if method[0] == "opt_" + opt:
                assert isinstance(method[1], types.MethodType)
                print(inspect.getdoc(method[1]))
                return
        print("No documentation of \"{}\"".format(opt))

    def method(self, opt: str) -> typing.Callable:
        """Returns the actual option method from a user-friendly option name."""
        for method in self._option_methods():
            if method[0] == "opt_" + opt:
                assert isinstance(method[1], types.MethodType)
                return method[1]
        cli_error("unknown option: {}".format(opt))


################################################################################
#                                                                              #
#   Command classes                                                            #
#                                                                              #
################################################################################

class Host(CommandBase):
    """
    Create, delete or edit host.
        host <option> <argument(s)>
    """

    def opt_info(self, args: typing.List[str]) -> None:
        """
        info <name|ip>
            Print information about host. If <name> is an alias the cname hosts info is shown.
        """
        if len(args) < 1:
            name_or_ip = input("Enter name or ip> ")
        else:
            name_or_ip = args[0]

        # Get host info or raise exception
        info = host_info_by_name_or_ip(name_or_ip)

        # Pretty print all host info
        print_host_name(info["name"])
        print_contact(info["contact"])
        if info["comment"]:
            print_comment(info["comment"])
        print_ipaddresses(info["ipaddress"])
        print_ttl(info["ttl"])
        if info["hinfo"]:
            print_hinfo(hinfo_id_to_strings(info["hinfo"]))
        if info["loc"]:
            print_loc(info["loc"])
        for cname in aliases_of_host(info["name"]):
            print_cname(cname, info["name"])
        for txt in info["txt"]:
            print_txt(txt["txt"])
        cli_info("printed host info for {}".format(info["name"]))

    def opt_remove(self, args: typing.List[str]) -> None:
        """
        remove <name|ip>
            Remove host. If <name> is an alias the cname host is removed.
        """
        if len(args) < 1:
            name_or_ip = input("Enter name or ip> ")
        else:
            name_or_ip = args[0]

        info = host_info_by_name_or_ip(name_or_ip)

        if len(info["ipaddress"]) > 1 and "y" not in args:
            cli_warning("{} has multiple ipaddresses, must force")

        # Require force if host has any aliases
        aliases = aliases_of_host(info["name"])
        if len(aliases):
            if "y" not in args:
                cli_warning("{} has {} aliases, must force".format(info["name"], len(aliases)))
            else:
                for alias in aliases:
                    url = "http://{}:{}/hosts/{}".format(
                        conf["server_ip"],
                        conf["server_port"],
                        alias,
                    )
                    delete(url)
                    cli_info("deleted alias host {} when removing {}".format(alias, info["name"]))

        # TODO FORCE: kreve force hvis host har:  SRV eller NAPTR pekende på seg

        # Delete host
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        delete(url)
        cli_info("removed {}".format(info["name"]), print_msg=True)

    def opt_add(self, args: typing.List[str]) -> None:
        """
        add <name> <ip/net> <contact> [-hinfo <hinfo>] [-comment <comment>]
            Add a new host with the given name, ip or subnet and contact. hinfo and comment
            are optional.
        """
        hi_list = hinfo_list()
        if len(args) < 3:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            ip_or_net = input("Enter subnet or ip> ") if len(args) < 2 else args[1]
            contact = input("Enter contact> ")
            hinfo = input("Enter hinfo (optional)> ")
            while hinfo == "?":
                print_hinfo_list(hi_list)
                hinfo = input("Enter hinfo (optional)> ")
            comment = input("Enter comment (optional)> ")
        else:
            name = args[0]
            ip_or_net = args[1]
            contact = args[2]
            hinfo = "" if "-hinfo" not in args else args[args.index("-hinfo") + 1]
            comment = "" if "-comment" not in args else args[args.index("-comment") + 1]

        # Verify hinfo id
        if hinfo:
            hinfo = int(hinfo)
            if not 0 < hinfo <= len(hi_list):
                cli_warning("invalid hinfo ({}) when trying to add {}".format(hinfo, name))


        # Handle arbitrary ip from subnet if received a subnet
        if re.match(r"^.*\/$", ip_or_net):
            subnet = get_subnet(ip_or_net[:-1])
            ip = choose_ip_from_subnet(subnet)
        elif is_valid_subnet(ip_or_net):
            subnet = get_subnet(ip_or_net)
            ip = choose_ip_from_subnet(subnet)
        else:
            # check that the address given isn't reserved
            subnet = get_subnet(ip_or_net)
            network_object = ipaddress.ip_network(subnet['range'])
            addresses = list(network_object.hosts())
            reserved_addresses = set([str(ip) for ip in addresses[:subnet['reserved']]])
            if ip_or_net in reserved_addresses and 'y' not in args:
                cli_warning("Address is reserved. Requires force")
            if ip_or_net == network_object.network_address.exploded:
                cli_warning("Can't overwrite the network address of the subnet")
            if ip_or_net == network_object.broadcast_address.exploded:
                cli_warning("Can't overwrite the broadcast address of the subnet")
            ip = ip_or_net


        # Handle if subnet is frozen
        if 'y' not in args and subnet['frozen']:
            cli_warning("Subnet {} is frozen. Requires force".format(subnet['range']))

        # Contact sanity check
        if not is_valid_email(contact):
            cli_warning("invalid mail address ({}) when trying to add {}".format(contact, name))

        # Check if given host exists on either short or long form
        try:
            name = resolve_input_name(name)
        except HostNotFoundWarning:
            pass
        else:
            if "y" not in args:
                cli_warning("host {} already exists, must force".format(name))
            else:
                url = "http://{}:{}/hosts/{}".format(
                    conf["server_ip"],
                    conf["server_port"],
                    name,
                )
                delete(url)
                # NOTE: Could need a "regret" functionality if delete succeeds but post fails
                cli_info("deleted existing host {}".format(name))

        # Always use long form host name
        name = name if is_longform(name) else to_longform(name)

        # Create the new host with an ip address
        url = "http://{}:{}/hosts/".format(conf["server_ip"], conf["server_port"])
        post(url, name=name, ipaddress=ip, contact=contact or None,
             hinfo=hinfo or None, comment=comment or None)
        cli_info("created host {}".format(name), print_msg=True)

    def opt_set_contact(self, args: typing.List[str]) -> None:
        """
        set_contact <name> <contact>
            Set contact for host. If <name> is an alias the cname host is updated.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            contact = input("Enter contact> ")
        else:
            name = args[0]
            contact = args[1]

        # Contact sanity check
        if not is_valid_email(contact):
            cli_warning("invalid mail address {} (target host: {})".format(contact, name))

        # Get host info for <name> or its cname
        info = host_info_by_name(name)

        # Update contact information
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        patch(url, contact=contact)
        cli_info("Updated contact of {} to {}".format(info["name"], contact))

    def opt_set_comment(self, args: typing.List[str]) -> None:
        """
        set_comment <name> <comment>
            Set comment for host. If <name> is an alias the cname host is updated.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            comment = input("Enter comment> ")
        else:
            name = args[0]
            comment = " ".join(args[1:])

        # Get host info for <name> or its cname
        info = resolve_input_name(name)

        # Update comment
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        patch(url, comment=comment)
        cli_info("updated comment of {} to \"{}\"".format(info["name"], comment))

    def opt_rename(self, args: typing.List[str]) -> None:
        """
        rename <old-name> <new-name>
            Rename host. If <old-name> is an alias then the alias is renamed.
        """
        if len(args) < 2:
            old_name = input("Enter old name> ") if len(args) < 1 else args[0]
            new_name = input("Enter new name> ")
        else:
            old_name = args[0]
            new_name = args[1]

        old_name = resolve_input_name(old_name)

        # Require force if the new name is already in use
        try:
           info = host_info_by_name(new_name, follow_cnames=False)
        except HostNotFoundWarning:
            pass
        else:
            if "y" not in args:
                # QUESTION: should inform if the existing host has any records (like remove)?
                cli_warning("host {} already exists, must force".format(info["name"]))
            for alias in aliases_of_host(info["name"]):
                url = "http://{}:{}/hosts/{}".format(
                    conf["server_ip"],
                    conf["server_port"],
                    alias,
                )
                delete(url)
                cli_info("deleted alias host {} when removing {} before renaming {}".format(
                    alias,
                    info["name"],
                    old_name,
                ))
            # TODO FORCE: check and remove SRV, NAPTR pointing at existing host
            url = "http://{}:{}/hosts/{}".format(
                conf["server_ip"],
                conf["server_port"],
                info["name"],
            )
            delete(url)
            # NOTE: Could need a "regret" functionality if delete succeeds but post fails
            cli_info("deleted existing host {}".format(new_name))

        # Always use long form host name
        new_name = new_name if is_longform(new_name) else to_longform(new_name)

        # Rename host
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], old_name)
        patch(url, name=new_name)
        cli_info("renamed {} to {}".format(old_name, new_name))

        url = "http://{}:{}/cnames/?cname={}".format(
            conf["server_ip"],
            conf["server_port"],
            old_name,
        )
        cnames = get(url).json()
        for cname in cnames:
            url = "http://{}:{}/cnames/{}".format(
                conf["server_ip"],
                conf["server_port"],
                cname["id"],
            )
            patch(url, cname=new_name)

        # TODO SRV: Update all SRV pointing at host when renaming it
        # TODO NAPTR: Update all NAPTR pointing at host when renaming it

    def opt_a_add(self, args: typing.List[str]) -> None:
        """
        a_add <name> <ip|subnet>
            Add an A record to host. If <name> is an alias the cname host is used.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            ip_or_subnet = input("Enter ip/subnet> ")
        else:
            name = args[0]
            ip_or_subnet = args[1]

        # Get host info for <name> or its cname
        info = host_info_by_name(name)

        # Verify ip or get ip from subnet
        if is_valid_ipv4(ip_or_subnet):
            ip = ip_or_subnet
        elif is_valid_subnet(ip_or_subnet):
            # TODO SUBNET: choose random ip (?)
            cli_warning("subnets not implemented")
            ip = choose_ip_from_subnet(ip_or_subnet)
        else:
            cli_warning("invalid ipv4 nor subnet: \"{}\" (target host: {})".format(
                ip_or_subnet,
                info["name"])
            )

        # Add A record
        url = "http://{}:{}/ipaddresses/".format(conf["server_ip"], conf["server_port"])
        post(url, hostid=(info["hostid"]), ipaddress=ip)
        cli_info("added ip {} to {}".format(ip, info["name"]))

    def opt_a_remove(self, args: typing.List[str]) -> None:
        """
        a_remove <name> <ip>
            Remove A record from host. If <name> is an alias the cname host is used.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            ip = input("Enter ip> ")
        else:
            name = args[0]
            ip = args[1]

        # Ip sanity check
        if not is_valid_ipv4(ip):
            cli_warning("not a valid ipv4: \"{}\"".format(ip))

        # Check that ip belongs to host
        info = host_info_by_name(name)
        found = False
        for rec in info["ipaddress"]:
            if rec["ipaddress"] == ip:
                found = True
                break
        if not found:
            cli_warning("{} is not owned by {}".format(ip, info["name"]))

        # Remove ip
        url = "http://{}:{}/ipaddresses/{}".format(conf["server_ip"], conf["server_port"], ip)
        delete(url)
        cli_info("removed ip {} from {}".format(ip, info["name"]))

    def opt_a_change(self, args: typing.List[str]) -> None:
        """
        a_change <name> <old-ip> <new-ip-or-subnet>
            Change A record. If <name> is an alias the cname host is used.
        """
        if len(args) < 3:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            old_ip = input("Enter old ip> ") if len(args) < 2 else args[1]
            ip_or_subnet = input("Enter new ip/subnet> ")
        else:
            name = args[0]
            old_ip = args[1]
            ip_or_subnet = args[2]

        # Ip and subnet sanity checks
        if not is_valid_ipv4(old_ip):
            cli_warning("invalid ipv4 \"{}\" (target host {})".format(old_ip, name))
        elif not is_valid_ipv4(ip_or_subnet) and not is_valid_subnet(ip_or_subnet):
            cli_warning(
                "invalid ipv4 nor subnet \"{}\" (target host {})".format(ip_or_subnet, name))

        # Check that ip belongs to host
        info = host_info_by_name(name)
        found = False
        for rec in info["ipaddress"]:
            if rec["ipaddress"] == old_ip:
                found = True
                break
        if not found:
            cli_warning("{} is not owned by {}".format(old_ip, info["name"]))

        # Handle arbitrary ip from subnet if received a subnet
        if is_valid_ipv4(ip_or_subnet):
            ip = ip_or_subnet
        else:
            # TODO SUBNET: choose random ip from subnet
            cli_warning("subnets not implemented")
            ip = choose_ip_from_subnet(ip_or_subnet)

        # Update A record ip address
        url = "http://{}:{}/ipaddresses/{}".format(conf["server_ip"], conf["server_port"], old_ip)
        patch(url, ipaddress=ip)
        cli_info("updated ip {} to {} for {}".format(old_ip, ip, info["name"]))

    def opt_a_show(self, args: typing.List[str]) -> None:
        """
        a_show <name>
            Show hosts ipaddresses. If <name> is an alias the cname host is used.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        print_ipaddresses(info["ipaddress"])
        cli_info("showed ip addresses for {}".format(info["name"]))

    def opt_aaaa_add(self, args: typing.List[str]) -> None:
        """
        aaaa_add <name> <ipv6>
            Add an AAAA record to host. If <name> is an alias the cname host is used.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            ip = input("Enter ipv6> ")
        else:
            name = args[0]
            ip = args[1]

        # Verify host and get host id
        info = host_info_by_name(name)

        # Verify ip or get ip from subnet
        if not is_valid_ipv6(ip):
            cli_warning("not a valid ipv6 \"{}\" (target host {})".format(ip, info["name"]))

        # Create AAAA records
        url = "http://{}:{}/ipaddresses/".format(conf["server_ip"], conf["server_port"])
        post(url, hostid=(info["hostid"]), ipaddress=ip)
        cli_info("added ip {} to {}".format(ip, info["name"]))

    def opt_aaaa_remove(self, args: typing.List[str]) -> None:
        """
        aaaa_remove <name> <ipv6>
            Remove AAAA record from host. If <name> is an alias the cname host is used.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            ip = input("Enter ipv6> ")
        else:
            name = args[0]
            ip = args[1]

        info = host_info_by_name(name)

        # Ipv6 sanity check
        if not is_valid_ipv6(ip):
            cli_warning("not a valid ipv6 \"{}\" (target host {})".format(ip, info["name"]))

        # Check that ip belongs to host
        found = False
        for rec in info["ipaddress"]:
            if rec["ipaddress"] == ip:
                found = True
                break
        if not found:
            cli_warning("{} is not owned by {}".format(ip, info["name"]))

        # Delete AAAA record
        url = "http://{}:{}/ipaddresses/{}".format(conf["server_ip"], conf["server_port"], ip)
        delete(url)
        cli_info("removed {} from {}".format(ip, info["name"]))

    def opt_aaaa_change(self, args: typing.List[str]) -> None:
        """
        aaaa_change <name> <old-ipv6> <new-ipv6>
            Change AAAA record. If <name> is an alias the cname host is used.
        """
        if len(args) < 3:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            old_ip = input("Enter old ipv6> ") if len(args) < 2 else args[1]
            new_ip = input("Enter new ipv6> ")
        else:
            name = args[0]
            old_ip = args[1]
            new_ip = args[2]

        info = host_info_by_name(name)

        # Ipv6 sanity checks
        if not is_valid_ipv6(old_ip):
            cli_warning("not a valid ipv6 \"{}\" (target host {})".format(old_ip, info["name"]))
        elif not is_valid_ipv6(new_ip):
            cli_warning("not a valid ipv6 \"{}\" (target host {})".format(new_ip, info["name"]))

        # Check that ip belongs to host
        found = False
        for rec in info["ipaddress"]:
            if rec["ipaddress"] == old_ip:
                found = True
                break
        if not found:
            cli_warning("\"{}\" is not owned by {}".format(old_ip, info["name"]))

        # Update AAAA records ip address
        url = "http://{}:{}/ipaddresses/{}".format(conf["server_ip"], conf["server_port"], old_ip)
        patch(url, ipaddress=new_ip)
        cli_info("changed ip {} to {} for {}".format(old_ip, new_ip, info["name"]))

    def opt_aaaa_show(self, args: typing.List[str]) -> None:
        """
        aaaa_show <name>
            Show hosts ipaddresses. If <name> is an alias the cname host is used.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        print_ipaddresses(info["ipaddress"])
        cli_info("showed aaaa records for {}".format(info["name"]))

    def opt_ttl_set(self, args: typing.List[str]) -> None:
        """
        ttl_set <name> <ttl>
            Set ttl for host. Valid values are 300 <= TTL <= 68400 or "default". If <name> is an
            alias the alias host is updated.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            ttl = input("Enter ttl> ")
        else:
            name = args[0]
            ttl = args[1]

        host_name = resolve_input_name(name)

        # TTL sanity check
        if not is_valid_ttl(ttl):
            cli_warning("invalid TTL value: {} (target host {})".format(ttl, host_name))

        # Update TTL
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], host_name)
        patch(url, ttl=ttl if ttl != "default" else -1)
        cli_info("updated TTL for {}".format(host_name))

    def opt_ttl_remove(self, args: typing.List[str]) -> None:
        """
        ttl_remove <name>
            Remove explicit TTL for host. If <name> is an alias the alias host is updated.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        host_name = resolve_input_name(name)

        # Remove TTL value
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], host_name)
        patch(url, ttl=-1)
        cli_info("removed TTL for {}".format(host_name))

    def opt_ttl_show(self, args: typing.List[str]) -> None:
        """
        ttl_show <name>
            Show ttl for host. If <name> is an alias the alias hosts TTL is shown.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        print_ttl(info["ttl"])
        cli_info("showed TTL for {}".format(info["name"]))

    def opt_cname_add(self, args: typing.List[str]) -> None:
        """
        cname_add <existing-name> <new-alias>
            Add a CNAME record to host. If <existing-name> is an alias the cname host is used as
            target for <new-alias>.
        """
        if len(args) < 2:
            name = input("Enter name> ") if len(args) < 1 else args[0]
            alias = input("Enter alias> ")
        else:
            name = args[0]
            alias = args[1]

        host_info = host_info_by_name(name)

        # If alias name already exists the host cannot have any records
        try:
            alias_info = host_info_by_name(alias)
        except HostNotFoundWarning:
            alias_info = None
        else:
            if alias_info["hinfo"] or \
                    alias_info["loc"] or \
                    alias_info["cname"] or \
                    alias_info["ipaddress"] or \
                    alias_info["txt"]:
                cli_warning("host {} already exists and has record(s)".format(alias_info["name"]))

        # Create cname host if it doesn't exist
        if not alias_info:
            alias = alias if is_longform(alias) else to_longform(alias)
            url = "http://{}:{}/hosts/".format(conf["server_ip"], conf["server_port"])
            post(url, name=alias, contact=host_info["contact"])
            alias_info = host_info_by_name(alias)

        # Create CNAME record
        url = "http://{}:{}/cnames/".format(conf["server_ip"], conf["server_port"])
        post(url, hostid=alias_info["hostid"], cname=host_info["name"])
        cli_info("Added cname alias {} for {}".format(alias_info["name"], host_info["name"]))

    def opt_cname_remove(self, args: typing.List[str]) -> None:
        """
        cname_remove <name> <alias-to-delete>
            Remove CNAME record.
        """
        if len(args) < 2:
            name = input("Enter name> ") if len(args) < 1 else args[0]
            alias = input("Enter alias> ")
        else:
            name = args[0]
            alias = args[1]

        host_name = resolve_input_name(name)
        alias_info = host_info_by_name(alias, follow_cnames=False)

        # Check that cname host is an alias for host
        cnames = alias_info["cname"]
        if len(cnames) < 1:
            cli_warning("\"{}\" doesn't have any CNAME records.".format(alias_info["name"]))
        if cnames[0]["cname"] != host_name:
            cli_warning("\"{}\" is not an alias for \"{}\"".format(alias_info["name"], host_name))

        # Delete CNAME host
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"],
                                              alias_info["name"])
        delete(url)
        cli_info("Removed cname alias {} for {}".format(alias_info["name"], host_name))

    def opt_cname_show(self, args: typing.List[str]) -> None:
        """
        cname_show <name>
            Show CNAME records for host. If <name> is an alias the cname hosts aliases are shown.
        """
        name = input("Enter name> ") if len(args) < 1 else args[0]

        # Gets the host info of the named host or the cname host if name is an alias
        info = host_info_by_name(name)

        for alias in aliases_of_host(info["name"]):
            print_cname(alias, info["name"])
        cli_info("showed cname aliases for {}".format(info["name"]))

    def opt_loc_set(self, args: typing.List[str]) -> None:
        """
        loc_set <name> <loc>
            Set location of host. If <name> is an alias the cname host is updated.
        """
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            loc = input("Enter loc> ")
        else:
            name = args[0]
            loc = " ".join(args[1:])

        info = host_info_by_name(name)

        # LOC sanity check
        if not is_valid_loc(loc):
            cli_warning("invalid LOC \"{}\" (target host {})".format(loc, info["name"]))

        # Update LOC
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        patch(url, loc=loc)
        cli_info("updated LOC to {} for {}".format(loc, info["name"]))

    def opt_loc_remove(self, args: typing.List[str]) -> None:
        """
        loc_remove <name>
            Remove location from host. If <name> is an alias the cname host is updated.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        patch(url, loc="")
        cli_info("removed LOC for {}".format(info["name"]))

    def opt_loc_show(self, args: typing.List[str]) -> None:
        """
        loc_show <name>
            Show location of host. If <name> is an alias the cname hosts LOC is shown.
        """
        name = input("Enter name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        print_loc(info["loc"])
        cli_info("showed LOC for {}".format(info["name"]))

    def opt_hinfo_set(self, args: typing.List[str]) -> None:
        """
        hinfo_set <name> <hinfo>
            Set hinfo for host. If <name> is an alias the cname host is updated.
        """
        hi_list = hinfo_list()
        if len(args) < 2:
            name = input("Enter host name> ") if len(args) < 1 else args[0]
            hinfo = input("Enter hinfo> ")
            while hinfo == "?":
                print_hinfo_list(hi_list)
                hinfo = input("Enter hinfo> ")
        else:
            name = args[0]
            hinfo = args[1]

        # Hinfo sanity check
        hinfo = int(hinfo)
        if not 0 < hinfo <= len(hi_list):
            cli_warning("invalid hinfo.")

        info = host_info_by_name(name)

        # Update hinfo
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        patch(url, hinfo=hinfo)
        cli_info("updated hinfo to {} for {}".format(hinfo, info["name"]))

    def opt_hinfo_remove(self, args: typing.List[str]) -> None:
        """
        hinfo_remove <name>
            Remove hinfo for host. If <name> is an alias the cname host is updated.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        url = "http://{}:{}/hosts/{}".format(conf["server_ip"], conf["server_port"], info["name"])
        patch(url, hinfo=-1)
        cli_info("removed hinfo for {}".format(info["name"]))

    def opt_hinfo_show(self, args: typing.List[str]) -> None:
        """
        hinfo_show <name>
            Show hinfo for host. If <name> is an alias the cname hosts hinfo is shown.
        """
        name = input("Enter host name> ") if len(args) < 1 else args[0]
        info = host_info_by_name(name)
        print_hinfo(hinfo_id_to_strings(info["hinfo"]))
        cli_info("showed hinfo for {}".format(info["name"]))

    def opt_used_list(self, args: typing.List[str]) -> None:
        """
        used_list <ip>
            List addresses used on the subnet which <ip> belongs to.
        """
        # TODO: implementer used_list
        pass


class History(CommandBase):
    """
    Show history or redo/undo actions.
    """

    def opt_print(self, args: typing.List[str]):
        """
        print
            Print the history.
        """
        history.print()

    def opt_redo(self, args: typing.List[str]):
        """
        redo <history-number>
            Redo some history request(s) given by <history-number>. If the number is on the form
            "1.2" then request nr. 2 of command nr. 1 is redone. If it's on the form "1" then all
            requests of command nr. 1 is redone (GET requests are not redone if not explicitly
            numbered)
        """
        tmp = args[0].split(sep='.')
        if len(tmp) < 2:
            history.redo(int(tmp[0]))
        else:
            history.redo(int(tmp[0]), int(tmp[1]))

    def opt_undo(self, args: typing.List[str]):
        """
        undo <history-number>
            Undo some history request(s) given by <history-number>. If the number is on the form
            "1.2" then request nr. 2 of command nr. 1 is redone. If it's on the form "1" then all
            requests of command nr. 1 is redone (GET requests cannot be undone)
        """
        pass


class Subnet(CommandBase):
    """
    Handle subnets.
        subnet <option> <argument(s)>
    """

    def opt_info(self, args: typing.List[str]):
        """
        info <subnet>
            Display subnet info
        """
        if len(args) < 1:
            ip_range = input("Enter subnet> ")
        else:
            ip_range = args[0]

        # Get subnet info or raise exception
        subnet_info = get_subnet(ip_range)
        used_list = get_subnet_used_list(subnet_info['range'])
        network = ipaddress.ip_network(subnet_info['range'])

        # Pretty print all subnet info
        print_subnet(subnet_info['range'], "Subnet:")
        print_subnet(network.netmask.exploded, "Netmask:")
        print_subnet(subnet_info['description'], "Description:")
        print_subnet(subnet_info['category'], "Category:")
        print_subnet(subnet_info['location'], "Location:")
        print_subnet(subnet_info['vlan'], "VLAN")
        print_subnet(subnet_info['dns_delegated'] if subnet_info['dns_delegated'] else False, "DNS delegated:")
        print_subnet(subnet_info['frozen'] if subnet_info['frozen'] else False, "Frozen")
        print_subnet_reserved(subnet_info['range'], subnet_info['reserved'])
        print_subnet(len(used_list), "Used addresses:")
        print_subnet_unused(network.num_addresses - (subnet_info['reserved'] + 2)- len(used_list))
        cli_info("printed subnet info for {}".format(subnet_info['range']))

    def opt_create(self, args:typing.List[str]):
        """
        create <subnet> <description> <vlan> <dns_delegated> <category> <location> <frozen>
            Create a new subnet
        """
        ip_range = input("Enter subnet>") if len(args) < 1 else args[0]
        if not is_valid_subnet(ip_range): cli_warning("Not a valid netmask")

        description = input("Enter description>") if len(args) < 2 else args[1]

        vlan = input("Enter VLAN (optional)>") if len(args) < 3 else args[2]

        if vlan:
            try:
                vlan = int(vlan)
            except ValueError:
                cli_warning("Not a valid integer")

        category = input("Enter category (optional)>") if len(args) < 4 else args[3]
        if category and not is_valid_category_tag(category):
            cli_warning("Not a valid category tag")
        location = input("Enter location (optional)>") if len(args) < 5 else args[4]
        if location and not is_valid_location_tag(location):
            cli_warning("Not a valid location tag")

        frozen = input("Is the subnet frozen? y/n>") if len(args) < 6 else args[5]
        while frozen != 'y' and frozen != 'n':
            frozen = input("Is the subnet frozen? y/n>")
        frozen = True if frozen == 'y' else False

        url = "http://{}:{}/subnets/".format(conf["server_ip"], conf["server_port"])
        post(url, range=ip_range, description=description, vlan=vlan, category=category, location=location, frozen=frozen)
        cli_info("created subnet {}".format(ip_range), True)

    def opt_remove(self, args:typing.List[str]):
        """
        remove <subnet>
            Remove subnet
        """
        ip_range = input("Enter subnet>") if len(args) < 1 else args[0]
        if not is_valid_subnet(ip_range): cli_warning("Not a valid netmask")

        host_list = get_subnet_used_list(ip_range)
        if host_list:
            cli_warning("Subnet contains addresses that are in use. Remove hosts before deletion")

        if 'y' not in args:
            cli_warning("Must force (y)")

        url = "http://{}:{}/subnets/{}".format(conf["server_ip"], conf["server_port"], ip_range)
        delete(url)
        cli_info("removed subnet {}".format(ip_range), True)

    def opt_set_vlan(self, args: typing.List[str]):
        """
        set_vlan <subnet> <vlan>
            Set VLAN for subnet
        """
        ip_range = input("Enter subnet>") if len(args) < 1 else args[0]
        subnet = get_subnet(ip_range)
        vlan = int(input("Enter new VLAN>") if len(args) < 2 else args[1])

        url = "http://{}:{}/subnets/{}".format(conf["server_ip"], conf["server_port"], subnet['range'])
        patch(url, vlan=vlan)
        cli_info("updated vlan to {} for {}".format(vlan, subnet['range']))

    def opt_set_description(self, args: typing.List[str]):
        """
        set_description <subnet> <description>
            Set description for subnet
        """
        ip_range = input("Enter subnet>") if len(args) < 1 else args[0]
        subnet = get_subnet(ip_range)
        description = input("Enter new description>") if len(args) < 2 else args[1]

        url = "http://{}:{}/subnets/{}".format(conf["server_ip"], conf["server_port"], subnet['range'])
        patch(url, description=description)
        cli_info("updated description to '{}' for {}".format(description, subnet['range']), True)

    def opt_set_location(self, args: typing.List[str]):
        """
        set_location <subnet> <location_tag>
            Set location tag for subnet
        """
        ip_range = input("Enter subnet>") if len(args) < 1 else args[0]
        subnet = get_subnet(ip_range)
        location_tag = input("Enter new location tag>") if len(args) < 2 else args[1]
        if not is_valid_location_tag(location_tag):
            cli_warning("Not a valid location tag")

        url = "http://{}:{}/subnets/{}".format(conf["server_ip"], conf["server_port"], subnet['range'])
        patch(url, location=location_tag)
        cli_info("updated location tag to '{}' for {}".format(location_tag, subnet['range']), True)

    def opt_set_category(self, args: typing.List[str]):
        """
        set_category <subnet> <category_tag>
            Set category tag for subnet
        """
        ip_range = input("Enter subnet>") if len(args) < 1 else args[0]
        subnet = get_subnet(ip_range)
        category_tag = input("Enter new category tag>") if len(args) < 2 else args[1]
        if not is_valid_category_tag(category_tag):
            cli_warning("Not a valid category tag")

        url = "http://{}:{}/subnets/{}".format(conf["server_ip"], conf["server_port"], subnet['range'])
        patch(url, category=category_tag)
        cli_info("updated category tag to '{}' for {}".format(category_tag, subnet['range']), True)

    def opt_set_dns_delegated(self, args: typing.List[str]):
        """
        set_dns_delegated <subnet>
            Set that DNS-administration is being handled elsewhere.
        """
        ip_range = input("Enter subnet>") if len(args) < 1 else args[0]
        subnet = get_subnet(ip_range)

        url = "http://{}:{}/subnets/{}".format(conf["server_ip"], conf["server_port"], subnet['range'])
        patch(url, dns_delegated=True)
        cli_info("updated dns_delegated to '{}' for {}".format(True, subnet['range']), True)

    def opt_unset_dns_delegated(self, args: typing.List[str]):
        """
        unset_dns_delegated <subnet>
            Set that DNS-administration is not being handled elsewhere.
        """
        ip_range = input("Enter subnet>") if len(args) < 1 else args[0]
        subnet = get_subnet(ip_range)

        url = "http://{}:{}/subnets/{}".format(conf["server_ip"], conf["server_port"], subnet['range'])
        patch(url, dns_delegated=False)
        cli_info("updated dns_delegated to '{}' for {}".format(False, subnet['range']), True)

    def opt_set_frozen(self, args: typing.List[str]):
        """
        set_frozen <subnet>
            Freeze a subnet.
        """
        ip_range = input("Enter subnet>") if len(args) < 1 else args[0]
        subnet = get_subnet(ip_range)

        url = "http://{}:{}/subnets/{}".format(conf["server_ip"], conf["server_port"], subnet['range'])
        patch(url, frozen=True)
        cli_info("updated frozen to '{}' for {}".format(True, subnet['range']), True)

    def opt_unset_frozen(self, args: typing.List[str]):
        """
        unset_frozen <subnet>
            Unfreeze a subnet.
        """
        ip_range = input("Enter subnet>") if len(args) < 1 else args[0]
        subnet = get_subnet(ip_range)

        url = "http://{}:{}/subnets/{}".format(conf["server_ip"], conf["server_port"], subnet['range'])
        patch(url, frozen=False)
        cli_info("updated frozen to '{}' for {}".format(False, subnet['range']), True)

    def opt_set_reserved(self, args: typing.List[str]):
        """
        set_reserved <subnet> <number>
            Set number of reserved hosts.
        """
        ip_range = input("Enter subnet>") if len(args) < 1 else args[0]
        subnet = get_subnet(ip_range)
        reserved = input("Enter number of reserved hosts>")

        try:
            reserved = int(reserved)
        except ValueError:
            cli_warning("Not a valid integer")

        url = "http://{}:{}/subnets/{}".format(conf["server_ip"], conf["server_port"], subnet['range'])
        patch(url, reserved=reserved)
        cli_info("updated reserved to '{}' for {}".format(reserved, subnet['range']), True)

    def opt_list_used_addresses(self, args: typing.List[str]):
        """
        list_used_addresses <subnet>
            Lists all the used addresses for a subnet
        """
        ip_range = input("Enter subnet>") if len(args) < 1 else args[0]

        if is_valid_ip(ip_range):
            subnet = get_subnet(ip_range)
            addresses = get_subnet_used_list(subnet['range'])
        elif is_valid_subnet(ip_range):
            addresses = get_subnet_used_list(ip_range)
        else:
            cli_warning("Not a valid ip or subnet")

        hosts = []
        for address in addresses:
            hosts.append(resolve_ip(address))

        for x in range(len(addresses)):
            print("{1:<{0}}{2}".format(25, addresses[x], hosts[x]))

    def opt_import(self, args: typing.List[str]):
        """
        import <file>
            Import subnet data from <file>.
        """
        input_file = input("Enter path to import file>") if len(args) < 1 else args[0]
        log_file = open('subnets_import.log', 'w+')
        vlans = get_vlan_mapping()
        ERROR = False # Flag to check before making requests if something isn't right

        log_file.write("------ READ FROM {} START ------\n".format(input_file))

        # Read in new subnet structure from file
        import_data = {}
        with open(input_file, 'r') as file:
            line_number = 0
            for line in file:
                line_number += 1
                match = re.match(r"(?P<range>\d+.\d+.\d+.\d+\/\d+)\s+:(?P<tags>.*):\|(?P<description>.*)", line)
                if match:
                    tags = match.group('tags').split(':')
                    info = {'location': None, 'category': ''}
                    for tag in tags:
                        if is_valid_location_tag(tag):
                            info['location'] = tag
                        elif is_valid_category_tag(tag):
                            info['category'] = ('%s %s' % (info['category'], tag)).strip()
                        else:
                            # TODO ERROR = True ?
                            log_file.write("{}: Invalid tag {}. Valid tags can be found in {}\n".format(line_number, tag, conf['tag_file']))
                    data = {
                        'range': match.group('range'),
                        'description': match.group('description').strip(),
                        'vlan': vlans[match.group('range')] if match.group('range') in vlans else 0,
                        'category': info['category'] if info['category'] else None,
                        'location': info['location'] if info['location'] else None,
                        'frozen': False
                    }
                    import_data['%s' % match.group('range')] = data

        log_file.write("------ READ FROM {} END ------\n".format(input_file))

        # Fetch existing subnets from server
        res = requests.get('http://{}:{}/subnets'.format(conf["server_ip"], conf["server_port"])).json()
        current_subnets = {subnet['range']: subnet for subnet in res}

        subnets_delete = current_subnets.keys() - import_data.keys()
        subnets_post = import_data.keys() - current_subnets.keys()
        subnets_patch = set()
        subnets_ignore = import_data.keys() & current_subnets.keys()

        # Check if subnets marked for deletion have any addresses in use
        for subnet in subnets_delete:
            used_list = get_subnet_used_list(subnet)
            if used_list:
                ERROR = True
                log_file.write("WARNING: {} contains addresses that are in use. Remove hosts before deletion\n".format(
                    {subnet['range']}))

        # Check if subnets marked for creation have any overlap with existing subnets
        for subnet_new in subnets_post:
            subnet_object = ipaddress.ip_network(subnet_new)
            for subnet_existing in subnets_patch:
                if subnet_object.overlaps(ipaddress.ip_network(subnet_existing)):
                    ERROR = True
                    log_file.write("ERROR: Overlap found between new subnet {} and existing subnet {}\n".format(subnet_new, subnet_existing))

        # Check which existing subnets need to be patched
        for subnet in subnets_ignore:
            current_data = current_subnets[subnet]
            new_data = import_data[subnet]
            if  (new_data['description'] != current_data['description'] \
                or new_data['vlan'] != current_data['vlan'] \
                or new_data['category'] != current_data['category'] \
                or new_data['location'] != current_data['location']):
                subnets_patch.add(subnet)

        if ERROR:
            cli_warning("Errors detected during setup. Check subnets_import.log for details")

        if ((len(subnets_delete) + len(subnets_patch)) / len(current_subnets.keys())) > 0.2 and 'y' not in args:
            cli_warning("WARNING: The import will change over 20% of the subnets. Requires force")

        log_file.write("------ API REQUESTS START ------\n".format(input_file))

        for subnet in subnets_delete:
            url = "http://{}:{}/subnets/{}".format(conf["server_ip"], conf["server_port"], subnet)
            delete(url)
            log_file.write("DELETE {}\n".format(url))

        for subnet in subnets_post:
            url = "http://{}:{}/subnets/".format(conf["server_ip"], conf["server_port"])
            data = import_data[subnet]
            post(url, range=data['range'], \
                description=data['description'], \
                vlan=data['vlan'], \
                category=data['category'], \
                location=data['location'], \
                frozen=data['frozen'])
            log_file.write("POST {} - {}\n".format(url, subnet))

        for subnet in subnets_patch:
            url = "http://{}:{}/subnets/{}".format(conf["server_ip"], conf["server_port"], subnet)
            data = import_data[subnet]
            patch(url, description=data['description'], \
                  vlan=data['vlan'], \
                  category=data['category'], \
                  location=data['location'])
            log_file.write("PATCH {}\n".format(url))

        log_file.write("------ API REQUESTS END ------\n".format(input_file))