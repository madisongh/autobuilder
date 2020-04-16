ABCFG_DICT = {}


def get_config_for_builder(name):
    return ABCFG_DICT[name]


def set_config_for_builder(name, val):
    ABCFG_DICT[name] = val


def settings_dict():
    return ABCFG_DICT


class WeeklySlot(object):
    def __init__(self, day, hour, minute):
        self.dayOfWeek = day
        self.hour = hour
        self.minute = minute


WEEKLY_SLOTS = [WeeklySlot(d, h, 0) for d in [5, 6] for h in [4, 8, 12, 16, 20]]
LAST_USED_WEEKLY = -1


def get_weekly_slot():
    global LAST_USED_WEEKLY
    try:
        slot = WEEKLY_SLOTS[LAST_USED_WEEKLY + 1]
        LAST_USED_WEEKLY += 1
    except IndexError:
        raise RuntimeError('too many weekly builds scheduled')
    return slot
