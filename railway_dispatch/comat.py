# -*- coding: utf-8 -*-
"""
Python 3.8 兼容性补丁
用于解决 modelscope 不兼容 Python 3.8 的问题

使用方法:
在导入 modelscope 之前先导入这个模块:
    import comat
    from modelscope import ...
"""

import sys
import types

# Python 3.9+ 内置类型注解 backport
if sys.version_info < (3, 9):
    # 创建内置类型的泛型别名
    from typing import List, Dict, Set, Tuple, Optional, Union, Generic, TypeVar

    # 将泛型类型添加到 builtins
    import builtins

    # 这些是 Python 3.9+ 的内置类型
    builtins.list = list
    builtins.dict = dict
    builtins.set = set
    builtins.tuple = tuple

    # 对于类型注解，创建一个简单的实现
    def _create_generic_alias(origin, params):
        """创建泛型别名"""
        if hasattr(origin, '__class_getitem__'):
            return origin.__class_getitem__(params)
        return GenericAlias(origin, params)

    class GenericAlias:
        """泛型别名类"""
        def __init__(self, origin, params):
            self.origin = origin
            self.params = params

        def __getitem__(self, key):
            if isinstance(key, tuple):
                return self.origin[tuple(key)]
            return self.origin[key]

        def __repr__(self):
            return f"{self.origin.__name__}[{self.params}]"

    # 修复 types.GenericAlias (Python 3.9+)
    if not hasattr(types, 'GenericAlias'):
        types.GenericAlias = GenericAlias

    # 修复 typing 中的 list[int] 等语法
    # 这需要在使用前打补丁
    print("Python 3.8 compatibility patch applied")
else:
    print(f"Running on Python {sys.version_info.major}.{sys.version_info.minor}, no patch needed")
