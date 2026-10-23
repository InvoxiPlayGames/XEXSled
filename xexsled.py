#!/usr/bin/env python3
# XEXSled - small tool to send an arbitrary XEX over the MCX image dl protocol
# Emma / InvoxiPlayGames, 2026

import socket
import struct
import sys
from pathlib import Path
from uuid import UUID

# version from the latest media center extender (from Win7) - v2.0.25960.4
# Xbox sanity checks this to match the one within the XEX, CHANGEME!
hardcoded_xex_version = 0x20000000 + (25960 << 8) + (4)

# header of every packet
packet_magic = 0x51C674C0

def find_xbox_ssdp():
    # set up a multicast socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(('0.0.0.0', 1900))
    multicast_request = socket.inet_aton("239.255.255.250") + socket.inet_aton("0.0.0.0")
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, multicast_request)
    print("Waiting for Xbox searching for PC...")
    while True:
        data, addr = s.recvfrom(1024)
        fullstr = data.decode("utf-8", errors="ignore")
        # lazyparse
        lines = fullstr.strip().splitlines()
        if not lines or lines[0] != "NOTIFY * HTTP/1.1":
            continue
        headers = {}
        for line in lines[1:]:
            if line == "":
                break
            try:
                key, value = line.split(":", 1)
            except ValueError:
                # errrrm?
                key = line
                value = None
            headers[key.strip()] = value.strip()
        # if this is the SSDP service check, parse it
        if headers["NTS"] == "ssdp:alive" and headers["NT"] == "urn:schemas-microsoft-com:nhed:presence:1":
            # lazyparse the USN to get the device UUID
            uuid = headers["USN"].split(":", 2)[1]
            uuid = UUID(uuid) # convert to uuid object
            # really ugly lazyparse for the AL value URNs
            ip = ""
            port = 0
            alsplit = headers["AL"].split("<")[1:]
            for split in alsplit:
                urnstr = split.strip(">")
                urnsplit = urnstr.split("?", 1)
                argsplit = urnsplit[1].split("&")
                args = {}
                for arg in argsplit:
                    argsplit2 = arg.split("=", 1)
                    args[argsplit2[0]] = argsplit2[1]
                if urnsplit[0] == "urn:schemas-microsoft-com:nhed:attributes":
                    print(f"Firmware version 2.0.{args["firmwarever"]}")
                elif urnsplit[0] == "urn:schemas-microsoft-com:nhed:download-target":
                    ip = args["address"]
                    port = int(args["port"])
            if ip != "" and port != 0:
                print("Found Xbox listening on %s:%i" % (ip, port))
                return (uuid, ip, port)

def build_opt_segment(type: int, data: bytearray | bytes):
    return struct.pack(">hh", type, len(data)) + data

def build_packet(type: int, segments: bytearray | bytes):
    return struct.pack(">ihh", packet_magic, type, len(segments)) + segments

def build_hello_packet(server_guid: UUID, client_guid: UUID, xex_length: int, xex_ver: int):
    segments = bytes()
    segments += build_opt_segment(0x0118, struct.pack("<i", 0)) # status code
    segments += build_opt_segment(0x0110, client_guid.bytes_le)
    segments += build_opt_segment(0x0113, server_guid.bytes_le)
    segments += build_opt_segment(0x0139, struct.pack("<i", xex_length))
    segments += build_opt_segment(0x019D, struct.pack("<i", xex_ver))
    return build_packet(0x0141, segments)

def get_response_status(s: socket, expected_type: int):
    # receive the response header
    resphdr = s.recv(0x8)
    if len(resphdr) != 0x8:
        print("Xbox hung up the connection. Not searching or bad XEX?")
        s.close()
        return -1
    magic, type, resplen = struct.unpack(">ihh", resphdr)
    if magic != packet_magic:
        print("Xbox sent an invalid response")
        s.close()
        return -2
    if type != expected_type or resplen != 0x8:
        print("Xbox sent a packet of the wrong type")
        s.close()
        return -3
    # receive the actual response
    resp = s.recv(resplen)
    type, _, status = struct.unpack(">hhi", resp)
    if type != 0x0118:
        print("Xbox sent an unknown response")
        s.close()
        return -4
    return status

def main(argc, argv):
    if (argc < 2):
        print("usage: %s [paired MCX GUID] [path/to/your.xex]" % (sys.argv[0]))
        return
    # extract arguments
    server_guid = UUID(argv[0])
    xex_file = Path(argv[1]).read_bytes()
    # TODO: parse xex version from the xex file itself
    client_guid, client_ip, client_port = find_xbox_ssdp()
    # connect to the server
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((client_ip, client_port))
    except ConnectionRefusedError:
        print("Failed to connect to Xbox")
        return
    except:
        print("Unknown error")
        return
    # send our hello packet
    print("Sending the Xbox our device + XEX information")
    print("XEX version: %08x, XEX length: %i" % (hardcoded_xex_version, len(xex_file)))
    hello = build_hello_packet(server_guid, client_guid, len(xex_file), hardcoded_xex_version)
    s.send(hello)
    status = get_response_status(s, 0x0122) # wait for answer to hello
    if status == 0x01000000: # parsed as big endian even tho its little. who care
        print("Xbox already has a cached XEX and is now booting that")
        return
    elif status != 0:
        print("Xbox returned unknown status code %08x" % (status))
        return
    print("Sending XEX file (%i bytes)" % (len(xex_file)))
    # we're ready to send our xex now
    s.send(xex_file)
    # wait for the response
    status = get_response_status(s, 0x0126)
    if status == 0:
        print("Xbox has taken our XEX and is launching it!")
    else:
        print("Xbox returned an error while parsing our XEX %08x" % (status))
    s.close()

if __name__ == "__main__":
    argc = len(sys.argv[1:])
    argv = sys.argv[1:]
    main(argc, argv)
