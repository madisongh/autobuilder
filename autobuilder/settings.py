ABCFG_DICT = {}

def get_config_for_builder(name):
    return ABCFG_DICT[name]

def set_config_for_builder(name, val):
    ABCFG_DICT[name] = val

def settings_dict():
    return ABCFG_DICT
