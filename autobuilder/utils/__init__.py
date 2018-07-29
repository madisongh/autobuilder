def dict_merge(*dict_args):
    result = {}
    for d in dict_args:
        result.update(d)
    return result
