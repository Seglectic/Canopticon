# Pi Appliance Networking Plan

## Current State

- `wlan0` is the active client connection managed by NetworkManager.
- `eth0` is present but currently unused.
- `avahi-daemon` is enabled, so the current hostname advertises as `zinger.local`.

## Practical AP Direction

The Raspberry Pi 4B only has one onboard Wi-Fi interface, so the clean appliance path is:

1. Manage the Pi over `eth0`.
2. Reconfigure `wlan0` from Wi-Fi client mode to Wi-Fi AP mode.
3. Serve the Canopticon web app on the AP subnet.
4. Advertise a friendly mDNS hostname for the appliance, ideally `sky.local`.

This avoids trying to make one Wi-Fi radio act as both client and AP at the same time.

## Remote Reconfiguration Safety

Yes, plugging the Pi into Ethernet is the right way to do this remotely.

Recommended sequence:

1. Connect `zinger` to Ethernet and confirm it gets an IP on `eth0`.
2. SSH to the Ethernet address or keep using an SSH alias that resolves to Ethernet.
3. Reconfigure `wlan0` into AP mode.
4. Test phone/client access to the AP and web UI.
5. Only after that, decide whether to remove the old Wi-Fi client profile.

If you want to keep simultaneous client Wi-Fi plus AP later, the simplest path is adding a second Wi-Fi adapter rather than forcing both roles onto one radio.

## Suggested NetworkManager Shape

Use NetworkManager for the whole appliance stack instead of mixing in hostapd or dnsmasq manually.

- Existing Ethernet profile:
  - `netplan-eth0`
- New Wi-Fi AP profile:
  - interface: `wlan0`
  - mode: `ap`
  - SSID: something like `Canopticon`
  - WPA2/WPA3 passphrase
  - IPv4 method: `shared`

`ipv4.method shared` lets NetworkManager handle local DHCP/NAT for clients on the AP network.

## Hostname Plan For `sky.local`

There are two workable approaches:

### Option 1: Rename the appliance host

Set the machine hostname to `sky`.

Pros:

- simplest
- native Avahi behavior
- `sky.local` just works

Tradeoff:

- `zinger.local` goes away unless you keep an SSH alias locally

### Option 2: Keep hostname `zinger` and add a `sky.local` alias

Keep the system hostname as-is and add an mDNS alias service.

Pros:

- preserves existing machine identity

Tradeoff:

- slightly more moving parts than just renaming the host

For an appliance, Option 1 is usually the cleaner end state.

## Proposed Rollout

### Phase 1: Safe Remote Test

- plug in Ethernet
- verify `eth0` connectivity
- create AP profile on `wlan0`
- keep Canopticon bound to `0.0.0.0:8009`
- join the AP from a phone and confirm the app loads

### Phase 2: Friendly Name

- either rename host to `sky`
- or add a `sky.local` alias

### Phase 3: Appliance Cleanup

- remove the old Wi-Fi client profile if no longer needed
- optionally make Ethernet management-only and Wi-Fi appliance-only

## Example NetworkManager Commands

These are the kinds of commands we would use once Ethernet is active:

```bash
sudo nmcli connection add type wifi ifname wlan0 con-name canopticon-ap ssid Canopticon
sudo nmcli connection modify canopticon-ap 802-11-wireless.mode ap 802-11-wireless.band bg
sudo nmcli connection modify canopticon-ap wifi-sec.key-mgmt wpa-psk
sudo nmcli connection modify canopticon-ap wifi-sec.psk 'choose-a-strong-passphrase'
sudo nmcli connection modify canopticon-ap ipv4.method shared ipv6.method disabled
sudo nmcli connection up canopticon-ap
```

If we choose the rename path for the hostname:

```bash
sudo hostnamectl set-hostname sky
```

## Recommendation

The next real move should be:

1. plug `zinger` into Ethernet
2. keep remote access over `eth0`
3. turn `wlan0` into an AP with NetworkManager
4. rename the appliance to `sky` once the AP works

