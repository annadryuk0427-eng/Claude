from .base import SupplierAdapter
from .omega import OmegaAdapter

# code постачальника -> клас адаптера. Новий постачальник з API: додайте клас і рядок сюди.
ADAPTERS = {
    "omega": OmegaAdapter,
}


def adapter_for(supplier_row) -> SupplierAdapter:
    return ADAPTERS.get(supplier_row["code"], SupplierAdapter)(supplier_row)
