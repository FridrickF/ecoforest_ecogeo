"""Shared constants for the Ecoforest Modbus-over-HTTP API."""


class DataTypes:
    Register = 1
    Coil = 2


class Operations:
    Get = {DataTypes.Coil: 2001, DataTypes.Register: 2002}
    Set = {DataTypes.Coil: 2011, DataTypes.Register: 2012}
