# Port Container Yard Optimization – Proof of Concept

A Python-based proof of concept for optimizing container allocation in a port yard through constraint-based scoring, logistics simulation, and performance evaluation.

## Overview

Container yard management is a complex logistics problem. Assigning containers to storage stacks requires considering physical restrictions, operational requirements, departure schedules, and handling efficiency.

An inappropriate allocation can lead to unnecessary equipment movements, container reshuffling (re-handles), inefficient space utilization, and delays during container retrieval.

This project was developed during an internship at **Marsa Maroc**. Its objective was to design and simulate a decision-support approach capable of recommending suitable storage stacks for incoming containers while respecting operational constraints and improving yard organization.

**This project is a theoretical proof of concept. It was developed and evaluated through simulations and was not deployed in Marsa Maroc's operational environment.**

## Objectives

- Identify suitable storage stacks for incoming containers.
- Respect operational and physical placement constraints.
- Group containers belonging to the same voyage whenever possible.
- Anticipate container departure dates to reduce retrieval difficulties.
- Minimize estimated RTG travel distance and handling time.
- Reduce potential container re-handling operations.
- Compare optimized allocation against a random placement baseline.

## Methodology

The solution follows a sequential allocation approach based on a weighted scoring algorithm.

### 1. Data Preparation

Two Excel datasets are used as inputs:

- `containers_pool.xlsx`: container information, including container ID, type, shipping line, voyage, import/export status, weight, and arrival/departure dates.
- `yard_structure_generated.xlsx`: yard structure, including zones, blocks, storage stacks, coordinates, maximum stacking height, capacity, and specific stack restrictions.

The data is processed using Pandas, with date conversion, data cleaning, and selection of relevant attributes.

### 2. Constraint-Based Placement

For each container, the algorithm evaluates the available storage stacks.

**Hard constraints** eliminate unsuitable candidate stacks, including:

- Maximum stacking height.
- Container type compatibility.
- Import/export zone compatibility.
- Reefer electrical-power requirements.
- Voyage pre-assignment compatibility.
- Weight and stacking restrictions.
- Departure-order accessibility.

**Soft constraints** influence the score of admissible stacks:

- Grouping containers by voyage.
- Grouping containers with similar departure dates.
- Stack occupancy and utilization.
- Shipping-line compatibility.
- Estimated RTG travel distance and handling time.
- Avoiding the unnecessary opening of new stacks.

### 3. Scoring Algorithm

Each candidate stack receives a weighted score based on its suitability for the container.

The algorithm selects the admissible stack with the **lowest score**.

After each assignment, the yard state is updated to account for the new container, including stack height, top-container weight, occupancy, and RTG position.

### 4. Simulation and Performance Evaluation

Two allocation strategies are compared:

- **Optimized allocation:** uses the constraint-based scoring algorithm.
- **Random allocation:** selects an available stack randomly as a baseline.

The resulting yard configurations are evaluated using:

| Metric | Description |
|---|---|
| RTG Travel Distance | Estimated total distance traveled by handling equipment |
| Handling Time | Estimated travel and container handling time |
| Re-handles | Estimated number of reshuffling operations required |
| Grouping Purity | Proportion of containers belonging to the dominant voyage group within a stack |
| Constraint Violations | Number of detected placement-rule violations |

## Results

The simulations presented in the internship report showed improvements for the optimized allocation compared with the random baseline:

- **RTG travel distance:** less than 5,000 m compared with nearly 30,000 m for random allocation.
- **Handling time:** approximately 60% lower.
- **Re-handles:** almost eliminated, compared with more than 250 in the random scenario.
- **Grouping quality:** most optimized stacks achieved a grouping purity of 1 (100% homogeneous by voyage).

These figures are simulation results under the project's assumptions and should not be interpreted as measured operational gains.

## Technology Stack

- **Python** – Core algorithm and simulation.
- **Pandas** – Data loading, cleaning, and manipulation.
- **NumPy** – Numerical operations.
- **Matplotlib** – Performance visualization.
- **Excel** – Input datasets.

## Project Workflow

1. Load container and yard datasets.
2. Clean and prepare the input data.
3. Initialize the yard structure and its constraints.
4. Evaluate candidate stacks for each container.
5. Calculate weighted placement scores.
6. Select the best admissible stack.
7. Update the yard and equipment state.
8. Simulate container retrieval and estimate re-handles.
9. Compare optimized and random allocation strategies.
10. Visualize and analyze performance metrics.

## Future Improvements

The current implementation provides a foundation for more advanced optimization approaches.

Potential extensions include:

- **Machine Learning:** predict container handling or reshuffling risks and help rank candidate stacks.
- **Advanced Optimization:** explore genetic algorithms, mathematical programming, or other optimization techniques.
- **Dynamic Allocation:** incorporate real-time container arrivals, departures, and changes in yard occupancy.
- **Operational Integration:** connect the decision-support system to real yard management data and operational systems, subject to validation and authorization.
- **Interactive Visualization:** develop a dashboard to visualize yard occupancy, container positions, and allocation recommendations.

## Project Context

Developed as an internship project within the Information Systems Department at **Marsa Maroc**, during the 2024–2025 academic year.

The project demonstrates how data processing and algorithmic optimization can be applied to a real-world logistics problem through a simulation-based proof of concept.
