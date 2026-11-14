# INFRASAGE — Architecture technique globale et état d'avancement

## 1. Présentation générale du projet

InfraSage est un système intelligent de diagnostic et d'optimisation des infrastructures informatiques.

Son objectif est de détecter les anomalies, identifier leurs causes probables et sélectionner intelligemment les tests à effectuer pour améliorer le diagnostic, tout en prenant en compte le coût énergétique et la latence des opérations.

L'architecture repose sur plusieurs composants indépendants :

- Collectors : récupération des données de l'infrastructure.
- Knowledge Catalog : connaissances diagnostiques et relations entre hypothèses et observations.
- Test Catalog : définition des tests et des moyens de collecte.
- Probability Model : probabilités conditionnelles utilisées pour le raisonnement bayésien.
- Diagnostic Adapter : transformation des données collectées en observations exploitables.
- Bayesian Engine : actualisation des probabilités des hypothèses.
- Planner : sélection du prochain test à effectuer.
- Orchestrators : coordination des collectes et de l'investigation.

---

## 2. Architecture générale proposée

Le fonctionnement global envisagé :

```text
                 INFRASTRUCTURE
                       |
          +------------+------------+
          |                         |
    Netdata Provider          Docker Provider
          |                         |
    NetdataCollector          DockerCollector
          |                         |
          +------------+------------+
                       |
             CollectionOrchestrator
                       |
              Infrastructure Snapshot
                       |
                Diagnostic Adapter
                       |
                 Test Execution
                       |
                Test Observation
                       |
       +---------------+----------------+
       |               |                |
 knowledge.yaml   tests.yaml    probabilities.json
       |               |                |
       +---------------+----------------+
                       |
                 Bayesian Engine
                       |
                 Updated Beliefs
                       |
                    Planner
                       |
              Next Diagnostic Test
                       |
              Diagnostic Orchestrator
                       |
              Continue / Stop / Abstain
                       |
               Diagnostic Result
```

Remarque : ce schéma représente l'architecture cible à définir ensemble. Le mode exact de déclenchement des tests et la coordination entre le Planner et les collectors restent à finaliser.

---

## 3. Couche de collecte — Collectors

Cette partie est actuellement développée.

### 3.1 NetdataCollector

Fichier : `backend/collectors/netdata.py`

Responsabilités :

- Récupérer les métriques CPU globales.
- Récupérer les métriques RAM globales.
- Découvrir les interfaces réseau.
- Récupérer les données réseau disponibles.
- Calculer certaines métriques dérivées.

Technologie : API REST Netdata et bibliothèque Python `requests`.

Exemple de données :

```python
{
    "source": "netdata",
    "cpu": {...},
    "ram": {...},
    "network": {...},
    "errors": {},
    "derived": {...}
}
```

### 3.2 DockerCollector

Fichier : `backend/collectors/docker.py`

Responsabilités :

- Récupérer les conteneurs Docker.
- Identifier leur nom, image et état.
- Récupérer leur état de santé lorsqu'il existe.
- Récupérer les ports exposés.
- Récupérer le nombre de redémarrages.
- Calculer le CPU individuel.
- Calculer la mémoire utilisée et la limite mémoire.

Technologie : Docker SDK for Python.

Netdata reste notre fournisseur principal de métriques système. DockerCollector est complémentaire et fournit notamment les informations individuelles des conteneurs qui ne sont pas disponibles dans notre configuration Netdata actuelle.

### 3.3 Collectors complémentaires envisagés

- `DockerLogsCollector` : récupération des logs applicatifs à la demande.
- `DockerEventsCollector` : événements Docker, éventuellement dans une version ultérieure.

Les logs ne doivent pas être récupérés systématiquement lors de la collecte initiale.

### 3.4 BaseCollector

Fichier : `backend/collectors/base.py`

Classe abstraite commune aux collectors, avec une méthode `collect()`.

Elle reste volontairement minimale.

---

## 4. CollectionOrchestrator

Fichier : `backend/core/collection_orchestrator.py`

Cette partie est implémentée et testée.

Responsabilités :

1. Initialiser NetdataCollector et DockerCollector.
2. Déclencher leurs collectes indépendamment.
3. Gérer les erreurs de chaque collector sans interrompre l'autre.
4. Rassembler les résultats dans une structure commune.

Exemple :

```python
{
    "netdata": {...},
    "docker": [...],
    "errors": {}
}
```

Le résultat constitue un snapshot de l'infrastructure.

La collecte initiale est pensée comme une opération batch : récupérer les informations principales au début de l'investigation, puis effectuer des collectes supplémentaires uniquement lorsque les tests diagnostiques le nécessitent.

---

## 5. Couche de connaissance — Catalogues

La proposition de séparation discutée est la suivante :

### 5.1 knowledge.yaml — What does the evidence mean?

Contient les connaissances diagnostiques.

Responsabilités :

- Définir les hypothèses possibles.
- Décrire les relations entre les observations et les hypothèses.
- Indiquer si une observation soutient ou contredit une hypothèse.

Exemple conceptuel :

```yaml
hypotheses:
  - id: redis_process_stopped
    description: Redis process is stopped

relationships:
  - hypothesis: redis_process_stopped
    test: redis_runtime_state
    outcome: stopped
    relationship: supports
```

### 5.2 tests.yaml — How do we obtain the evidence?

Décrit les tests disponibles et leurs moyens d'exécution.

Responsabilités :

- Identifier chaque test.
- Définir ses résultats possibles.
- Identifier son provider.
- Décrire l'opération permettant d'obtenir le résultat.
- Définir les informations nécessaires à son exécution.

Exemple conceptuel :

```yaml
tests:
  - id: redis_runtime_state
    provider: netdata
    operation: verified_metric_query
    outcomes:
      - running
      - stopped
      - unknown
```

L'opération doit correspondre à une fonctionnalité réellement disponible chez le provider.

### 5.3 probabilities.json — How strong is the evidence?

Contient les probabilités conditionnelles utilisées par le moteur bayésien.

Exemple :

```json
{
  "redis_runtime_state": {
    "redis_process_stopped": {
      "running": 0.05,
      "stopped": 0.95,
      "unknown": 0.0
    }
  }
}
```

Valeurs illustratives uniquement : les probabilités doivent être justifiées et documentées.

Les trois fichiers ont des responsabilités distinctes :

| Fichier | Question |
|---|---|
| knowledge.yaml | Que signifie cette observation ? |
| tests.yaml | Comment obtenir cette observation ? |
| probabilities.json | Quelle est la force probabiliste de cette observation ? |

Il faudra assurer la cohérence des identifiants entre les trois fichiers et vérifier que les distributions de probabilités sont valides.

---

## 6. Diagnostic Adapter

Composant à concevoir.

Son rôle est de faire le lien entre les données réelles des collectors et les observations symboliques attendues par le moteur bayésien.

Exemple :

Donnée réelle :

```python
cpu_percent = 95
```

Interprétation :

```python
outcome = "critical"
```

Création de l'observation :

```python
Observation(
    test_identifier="cpu_usage_test",
    target_identifier="server_01",
    execution_status=ExecutionStatus.COMPLETED,
    observed_outcome="critical"
)
```

L'Adapter ne doit pas effectuer lui-même les calculs bayésiens.

Il doit également gérer les données manquantes, les erreurs de collecte et les résultats inconnus.

Important : il faut distinguer une observation `unknown` d'une exécution `FAILED` ou `UNAVAILABLE`. Une collecte qui échoue ne doit pas être interprétée comme une preuve de panne.

---

## 7. Bayesian Engine

Fichier : `backend/diagnosis/bayesian.py`

Le moteur bayésien est chargé du raisonnement probabiliste.

Son rôle :

1. Recevoir les probabilités initiales des hypothèses.
2. Recevoir une définition de test.
3. Recevoir une observation.
4. Mettre à jour les probabilités des hypothèses.
5. Retourner le nouvel état probabiliste.

Il utilise notamment le théorème de Bayes :

\[
P(H|O)=\frac{P(O|H)P(H)}{P(O)}
\]

Le moteur doit rester indépendant de Netdata, Docker et des détails de collecte.

Il travaille avec des objets abstraits tels que `Hypothesis`, `DiagnosticTest`, `Observation` et `BeliefState`.

---

## 8. Planner

Fichier : `backend/optimization/planner.py`

Le Planner choisit le prochain test à exécuter.

Il prend en compte notamment :

- Le gain d'information attendu.
- Le coût énergétique estimé.
- La latence estimée.

L'objectif est de sélectionner les tests qui apportent le plus d'informations par rapport à leur coût.

Le Planner ne doit pas appeler directement les collectors.

Il choisit un test à partir des tests disponibles et de l'état actuel des probabilités.

La coordination de son choix avec le mécanisme d'exécution reste à intégrer.

---

## 9. Diagnostic Orchestrator

Fichier : `backend/core/orchestrator.py`

Une version existe déjà dans la branche de développement du coéquipier.

Responsabilités actuelles :

- Gérer une investigation séquentielle.
- Faire appel au Planner pour sélectionner un test.
- Appliquer les observations au moteur bayésien.
- Mettre à jour les croyances.
- Contrôler les conditions d'arrêt.
- Retourner un résultat d'investigation.

Le code actuel utilise des observations prédéfinies ou simule des résultats à partir d'une hypothèse réelle.

Il ne déclenche pas encore les collectors et ne réalise pas encore l'intégration avec les données réelles.

**Il ne faut pas confondre ce composant avec CollectionOrchestrator.**

- CollectionOrchestrator : coordonne la collecte.
- Diagnostic Orchestrator : coordonne l'investigation diagnostique.

---

## 10. Modèles et contrats déjà définis

Fichier : `backend/core/models.py`

Les classes suivantes existent déjà :

| Classe | Rôle |
|---|---|
| `Hypothesis` | Représente une cause possible |
| `DiagnosticTest` | Décrit un test, ses résultats et ses likelihoods |
| `Observation` | Représente le résultat d'une exécution |
| `BeliefState` | Contient les probabilités actuelles |
| `TestSelection` | Représente le choix du Planner |
| `InvestigationResult` | Résultat complet de l'investigation |
| `ExecutionStatus` | COMPLETED, FAILED, UNAVAILABLE |
| `Decision` | CONTINUE, DIAGNOSED, ABSTAIN |

Le catalogue proposé devra alimenter ces modèles existants plutôt que recréer des structures parallèles.

---

## 11. Arborescence actuelle du projet

```text
backend/
│
├── api/
│
├── collectors/
│   ├── __init__.py
│   ├── base.py
│   ├── netdata.py
│   ├── docker.py
│   ├── docker_logs.py
│   └── docker_events.py
│
├── core/
│   ├── __init__.py
│   ├── models.py
│   ├── orchestrator.py
│   └── collection_orchestrator.py
│
├── diagnosis/
│   ├── __init__.py
│   └── bayesian.py
│
├── optimization/
│   ├── __init__.py
│   └── planner.py
│
├── energy/
│   ├── __init__.py
│   └── meter.py
│
├── twin/
│   ├── __init__.py
│   └── manager.py
│
└── main.py
```

Les fichiers de catalogues pourront être organisés séparément, par exemple :

```text
backend/
└── knowledge/
    ├── knowledge.yaml
    ├── tests.yaml
    └── probabilities.json
```

Cette organisation est une proposition, pas encore une décision définitive.

---

## 12. État d'avancement

| Composant | État |
|---|---|
| BaseCollector | Existant |
| NetdataCollector | Implémenté et testé |
| DockerCollector | Implémenté et testé |
| CollectionOrchestrator | Implémenté et testé |
| DockerLogsCollector | Présent, intégration à finaliser |
| DockerEventsCollector | Présent, utilité à confirmer |
| Models | Défini |
| Bayesian Engine | Implémenté dans la branche du coéquipier |
| Diagnostic Orchestrator | Implémenté en mode simulation/prédéfinition |
| Planner | En développement / intégration à finaliser |
| knowledge.yaml | Proposition de catalogue |
| tests.yaml | Proposition de catalogue |
| probabilities.json | Proposition de catalogue |
| Diagnostic Adapter | À concevoir |
| Intégration réelle collecte → diagnostic | À réaliser |

---

## 13. Décisions à prendre ensemble

Avant de connecter les composants, il faut se mettre d'accord sur les interfaces suivantes :

1. **Catalogue des hypothèses :** comment les hypothèses seront-elles identifiées et chargées ?
2. **Catalogue des tests :** contiendra-t-il les opérations exécutables et les résultats possibles ?
3. **Probabilités :** comment les likelihoods seront-ils associés aux tests et aux hypothèses ?
4. **Exécution :** qui transforme le test choisi par le Planner en appel concret à un collector ?
5. **Diagnostic Adapter :** où sera définie l'interprétation des métriques en résultats symboliques ?
6. **Collection initiale :** comment le snapshot initial sera-t-il utilisé pour exécuter les tests ?
7. **Orchestration :** comment connecter le nouveau mécanisme d'exécution au `run_investigation()` existant sans dupliquer sa logique ?

### Objectif commun

Avoir une architecture modulaire dans laquelle :

- Les collectors ignorent les hypothèses diagnostiques.
- Les catalogues ignorent les détails internes des collectors.
- Le moteur bayésien ignore les sources de données.
- Le Planner ignore les détails d'exécution.
- L'orchestration assure la communication entre les composants.

Chaque partie doit avoir un contrat clair avec les autres, afin que les différents développements puissent avancer indépendamment puis être intégrés sans conflits.