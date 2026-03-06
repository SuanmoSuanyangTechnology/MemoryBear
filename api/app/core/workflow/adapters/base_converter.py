# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/26 14:32
from abc import ABC, abstractmethod

from app.core.workflow.variable.base_variable import DEFAULT_VALUE, VariableType


class BaseConverter(ABC):
    @staticmethod
    def _convert_string(var):
        try:
            return str(var)
        except:
            return DEFAULT_VALUE(VariableType.STRING)

    @staticmethod
    def _convert_boolean(var):
        try:
            return bool(var)
        except:
            return DEFAULT_VALUE(VariableType.BOOLEAN)

    @staticmethod
    def _convert_number(var):
        try:
            return float(var)
        except:
            return DEFAULT_VALUE(VariableType.NUMBER)

    @staticmethod
    def _convert_object(var):
        try:
            return dict(var)
        except:
            return DEFAULT_VALUE(VariableType.OBJECT)

    @staticmethod
    @abstractmethod
    def _convert_file(var):
        pass

    @staticmethod
    def _convert_array_string(var):
        try:
            return list(var)
        except:
            return DEFAULT_VALUE(VariableType.ARRAY_STRING)

    @staticmethod
    def _convert_array_number(var):
        try:
            return list(var)
        except:
            return DEFAULT_VALUE(VariableType.ARRAY_NUMBER)

    @staticmethod
    def _convert_array_boolean(var):
        try:
            return list(var)
        except:
            return DEFAULT_VALUE(VariableType.ARRAY_BOOLEAN)

    @staticmethod
    def _convert_array_object(var):
        try:
            return list(var)
        except:
            return DEFAULT_VALUE(VariableType.ARRAY_OBJECT)

    @staticmethod
    @abstractmethod
    def _convert_array_file(var):
        pass
