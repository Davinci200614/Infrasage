# INFRASAGE — Rapport d'avancement
## Responsable : Rayane
## Branche : feature/docker-collector

---

## 1. Présentation de la contribution

Dans le cadre du projet INFRASAGE, ma contribution porte principalement sur la couche de collecte des données d'infrastructure et son intégration avec le système central.

L'objectif est de récupérer des informations provenant des conteneurs Docker et de Netdata afin de fournir au moteur de diagnostic des données exploitables.

## 2. Collecteurs développés et validés

### 2.1. Collecte des métriques Netdata

Fichier principal : `backend/collectors/netdata.py`

- Récupération des métriques système via l'API Netdata.
- Collecte des informations CPU et mémoire.
- Récupération des métriques réseau.
- Récupération des métriques disque.
- Gestion des erreurs de collecte.

### 2.2. Collecte des informations Docker

Fichier principal : `backend/collectors/docker.py`

- Connexion à l'environnement Docker.
- Récupération de la liste des conteneurs.
- Collecte des informations sur leur état.
- Récupération des informations utiles à la surveillance de l'infrastructure.

### 2.3. Collecte des logs Docker

Fichier principal : `backend/collectors/docker_logs.py`

- Récupération des logs des conteneurs.
- Lecture des sorties standard et erreur.
- Analyse des signatures d'erreurs reconnues.
- Collecte à la demande pour un conteneur donné.

### 2.4. Collecte des événements Docker

Fichier principal : `backend/collectors/docker_events.py`

- Récupération des événements Docker.
- Identification des événements de démarrage et d'arrêt.
- Détection des séquences de redémarrage.
- Calcul des informations relatives à l'activité des conteneurs.

## 3. Architecture et intégration

### 3.1. Collection Orchestrator

Fichier : `backend/core/collection_orchestrator.py`

Développement de l'orchestrateur de collecte permettant de centraliser les différents collecteurs.

Fonctionnalités :
- Collecte initiale des données Netdata et Docker.
- Collecte à la demande des logs et événements.
- Centralisation des résultats.
- Gestion des erreurs afin d'éviter qu'une erreur isolée bloque toute la collecte.

### 3.2. Collector Manager

Fichier : `backend/core/collector_manager.py`

Intégration de la couche de collecte avec le reste du système.

Fonctionnalités :
- Centralisation de l'accès aux données collectées.
- Exposition d'une méthode de collecte initiale.
- Gestion des collectes complémentaires.
- Préparation des données destinées au moteur de diagnostic.
- Respect du mode recherche (Research Mode).

L'exécution des tests de diagnostic réels reste volontairement désactivée en Phase 1 conformément aux restrictions du Research Mode.

### 3.3. Modèles et orchestration

Fichiers :
- `backend/core/models.py`
- `backend/core/orchestrator.py`

Contribution à la structuration des données et à leur intégration dans le processus d'investigation.

## 4. Intégration avec le diagnostic et la planification

Des contributions ont également été apportées aux modules suivants :

- `backend/diagnosis/bayesian.py` : éléments liés au raisonnement bayésien.
- `backend/diagnosis/catalogue_loader.py` : chargement et validation des catalogues.
- `backend/diagnosis/example.py` : exemple d'utilisation du système.
- `backend/optimization/planner.py` : éléments liés à la planification des tests.

Les catalogues du projet sont organisés dans le dossier `catalogues/`.

## 5. Tests et validation

### 5.1. Tests automatisés

La suite de tests du projet a atteint le résultat suivant :

**198 tests réussis.**

Les tests couvrent notamment :
- Les collecteurs.
- L'orchestrateur de collecte.
- Le gestionnaire de collecte.
- L'adaptation des données en observations.
- Le moteur bayésien.
- Le chargement des catalogues.
- La planification.
- L'investigation en simulation.

Un test de simulation de bout en bout vérifie également le déroulement d'une investigation, depuis la sélection d'un test jusqu'à la mise à jour des croyances et à la décision finale.

### 5.2. Validation dans un environnement réel

Des vérifications ont été effectuées avec Docker Desktop et un conteneur Netdata réel.

Résultats observés :

- Collecte réelle des métriques Netdata : réussie.
- Collecte des informations Docker : réussie.
- Récupération des logs d'un conteneur : réussie.
- Récupération des événements Docker : réussie.
- Détection d'une séquence de redémarrage : réussie.
- Intégration de la collecte initiale à travers `CollectorManager` : réussie.

Lors du test d'intégration, les données CPU, mémoire, réseau et disque ont été récupérées, ainsi que les informations des conteneurs Docker, sans erreur de collecte.

## 6. État actuel et limites

| Élément | État |
|---|---|
| Collecteur Netdata | Validé en environnement réel |
| Collecteur Docker | Validé en environnement réel |
| Collecteur Docker Logs | Validé en environnement réel |
| Collecteur Docker Events | Validé en environnement réel |
| Détection des redémarrages | Validée |
| Collection Orchestrator | Validé |
| Intégration initiale avec CollectorManager | Validée |
| Tests automatisés | 198 réussis |
| Investigation complète en simulation | Validée par les tests |
| Exécution réelle des tests de diagnostic | Désactivée en Phase 1 |
| Lancement complet de l'application | À vérifier |
| Modules Energy et Digital Twin | À examiner |
| Tests de performance | Reportés |

La validation réelle concerne la collecte des données et son intégration initiale. Elle ne constitue pas encore une validation complète de toute l'application INFRASAGE.

## 7. Fichiers principaux concernés

### Collecteurs
- `backend/collectors/docker.py`
- `backend/collectors/netdata.py`
- `backend/collectors/docker_logs.py`
- `backend/collectors/docker_events.py`
- `backend/collectors/base.py`

### Intégration
- `backend/core/collection_orchestrator.py`
- `backend/core/collector_manager.py`
- `backend/core/diagnostic_adapter.py`
- `backend/core/models.py`
- `backend/core/orchestrator.py`

### Diagnostic et optimisation
- `backend/diagnosis/bayesian.py`
- `backend/diagnosis/catalogue_loader.py`
- `backend/diagnosis/example.py`
- `backend/optimization/planner.py`

### Tests
- `tests/`

### Catalogues
- `catalogues/knowledge.yaml`
- `catalogues/tests.yaml`
- `catalogues/probabilities.json`

---

## 8. Conclusion

La couche de collecte d'INFRASAGE est opérationnelle et a été vérifiée à la fois par des tests automatisés et par des essais sur un environnement Docker réel.

L'intégration initiale avec le gestionnaire de collecte est également validée. Les prochaines étapes concernent la validation du fonctionnement global de l'application, l'examen des modules restants et les tests de performance.
