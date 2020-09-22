def dict_merge(*dict_args):
    result = {}
    for d in dict_args:
        if d:
            result.update(d)
    return result
