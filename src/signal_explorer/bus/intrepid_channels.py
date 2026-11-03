"""Known physical network layouts. Never infer a large layout for unknown models."""


def can_networks(api, device):
    name = device.Name
    kind = getattr(device, 'DeviceType', None)
    if kind == getattr(api, 'NEODEVICE_FIRE3', 15):
        return [(f'DWCAN_{i:02}', f'CAN{i}', getattr(api, f'NETID_DWCAN_{i:02}'))
                for i in range(1, 17)], 8
    names = ['HSCAN']
    if 'ValueCAN3' in name:
        names += ['MSCAN']
    elif 'ValueCAN4' in name:
        if '-1' not in name:
            names += ['HSCAN2']
        if '-4' in name:
            names += ['HSCAN3', 'HSCAN4']
    return [(n, f'CAN{i}', getattr(api, 'NETID_' + n)) for i, n in enumerate(names, 1)], (1 if kind == 10 else 0)
