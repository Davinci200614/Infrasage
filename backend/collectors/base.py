from abc import ABC, abstractmethod


class BaseCollector(ABC):
    """
    Classe de base pour tous les collectors d'InfraSage.
    """

    @abstractmethod
    def collect(self):
        pass