#
# Copyright (C) 2026 Anton
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Stand-in for the NS8 agent module used by configure-module unit tests."""

import os

_status = None


def set_weight(*_args, **_kwargs):
    return None


def set_status(status):
    global _status
    _status = status


def set_route(_data):
    return None


def get_route(_module_id):
    return {}


def read_envfile(file_path):
    env = {}
    with open(file_path, "r", encoding="utf-8") as handle:
        for line in handle:
            record = line.rstrip("\n")
            if record == "" or record[0] == "#":
                continue
            variable, value = record.split("=", 1)
            env[variable] = value
    return env


def write_envfile(file_path, envmap):
    entries = [ek + "=" + str(ev) for ek, ev in envmap.items()]
    entries.sort()
    payload = "\n".join(entries) + "\n"
    with open(file_path, "w", encoding="utf-8") as handle:
        handle.write(payload)


def _environment_path():
    return os.environ["AGENT_STATE_DIR"] + "/environment"


def set_env(var_name, var_value):
    path = _environment_path()
    envmap = read_envfile(path) if os.path.exists(path) else {}
    envmap[var_name] = var_value
    write_envfile(path, envmap)


def unset_env(var_name):
    path = _environment_path()
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    envmap = read_envfile(path)
    if var_name not in envmap:
        return
    del envmap[var_name]
    write_envfile(path, envmap)
