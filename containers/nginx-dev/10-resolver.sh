#!/bin/sh
# Generates /etc/nginx/resolver.conf from the first nameserver in the
# container's /etc/resolv.conf. That nameserver differs per environment
# (compose: the aardvark DNS on the network gateway; k8s: cluster DNS),
# so a fixed IP in nginx.conf does not work.
set -eu

ns="$(awk '/^nameserver[ \t]/ { print $2; exit }' /etc/resolv.conf)"
if [ -z "${ns}" ]; then
    echo "10-resolver.sh: geen nameserver gevonden in /etc/resolv.conf" >&2
    exit 1
fi
case "${ns}" in
    *:*) ns="[${ns}]" ;;
esac

# /etc/nginx/resolver.conf is a symlink to /tmp/resolver.conf (see
# Containerfile): /etc/nginx itself is not writable for the nginx user,
# and on k8s the whole rootfs is read-only.
echo "resolver ${ns} valid=10s ipv6=off;" > /etc/nginx/resolver.conf
echo "10-resolver.sh: resolver ${ns} (uit /etc/resolv.conf)"
