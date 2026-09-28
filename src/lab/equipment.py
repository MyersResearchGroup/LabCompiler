"""Lab-owned equipment identifiers. Backends resolve these to SDK definitions."""

from enum import Enum


class LiquidHandler(Enum):
    OT2 = "ot2"
    FLEX = "flex"
    STAR = "star"


class LabwareModel(Enum):
    CORNING_96_360_UL = "corning_96_360_ul"
    NEST_12_RESERVOIR_15_ML = "nest_12_reservoir_15_ml"
    NEST_96_PCR_100_UL = "nest_96_pcr_100_ul"
    OPENTRONS_96_PCR_200_UL = "opentrons_96_pcr_200_ul"
    BIORAD_96_PCR_200_UL = "biorad_96_pcr_200_ul"
    AZENTA_96_PCR_200_UL = "azenta_96_pcr_200_ul"
    OPENTRONS_24_COLD_BLOCK = "opentrons_24_cold_block"
    OPENTRONS_24_TUBE_RACK = "opentrons_24_tube_rack"
    OPENTRONS_15_CONICAL_RACK = "opentrons_15_conical_rack"


class CarrierModel(Enum):
    HAMILTON_PLATE_5 = "hamilton_plate_5"
    HAMILTON_TIP_5 = "hamilton_tip_5"


class ModuleModel(Enum):
    TEMPERATURE_GEN1 = "temperature_gen1"
    TEMPERATURE_GEN2 = "temperature_gen2"
    THERMOCYCLER_GEN1 = "thermocycler_gen1"
    THERMOCYCLER_GEN2 = "thermocycler_gen2"


class TipRackModel(Enum):
    OPENTRONS_20_UL = "opentrons_20_ul"
    OPENTRONS_300_UL = "opentrons_300_ul"
    FLEX_200_UL = "flex_200_ul"
    FLEX_50_UL = "flex_50_ul"
    HAMILTON_50_UL = "hamilton_50_ul"
    HAMILTON_300_UL = "hamilton_300_ul"


class PipetteModel(Enum):
    P20_SINGLE_GEN2 = "p20_single_gen2"
    P300_SINGLE_GEN2 = "p300_single_gen2"
    FLEX_1CHANNEL_1000 = "flex_1channel_1000"
    FLEX_1CHANNEL_50 = "flex_1channel_50"


class Mount(Enum):
    LEFT = "left"
    RIGHT = "right"


CARRIER_SITE_COUNTS = {
    CarrierModel.HAMILTON_PLATE_5: 5,
    CarrierModel.HAMILTON_TIP_5: 5,
}
MODULE_SITE_COUNTS = {
    ModuleModel.TEMPERATURE_GEN1: 1,
    ModuleModel.TEMPERATURE_GEN2: 1,
    ModuleModel.THERMOCYCLER_GEN1: 1,
    ModuleModel.THERMOCYCLER_GEN2: 1,
}
