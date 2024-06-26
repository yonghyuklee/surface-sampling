# Virtual Surface Site Relaxation-Monte Carlo (VSSR-MC)

## Contents
- [Overview](#overview)
- [System requirements](#system-requirements)
- [Setup](#setup)
- [Demo](#demo)


# Overview

This is the VSSR-MC algorithm for sampling surface reconstructions. VSSR-MC samples across both compositional and configurational spaces. It can interface with both a neural network potential (through ASE) or a classical potential (through ASE or LAMMPS). It is a key component of the Automatic Surface Reconstruction (AutoSurfRecon) pipeline described in the following work:

"Machine-learning-accelerated simulations to enable automatic surface reconstruction", by X. Du, J.K. Damewood, J.R. Lunger, R. Millan, B. Yildiz, L. Li, and R. Gómez-Bombarelli. https://doi.org/10.1038/s43588-023-00571-7

Please cite us if you find this work useful. Let us know in `issues` if you encounter any problems or have any questions.

To start, run `git clone git@github.com:learningmatter-mit/surface-sampling.git` to your local directory or a workstation.

Read through the following in order before running our code.

# System requirements

## Hardware requirements
We recommend a computer with the following specs:

- RAM: 16+ GB
- CPU: 4+ cores, 3 GHz/core

We tested out the code on machines with 6+ CPU cores @ 3.0+ GHz/core with 64+ GB of RAM.

To run with a neural network force field, a GPU is recommended. We ran on a single NVIDIA GeForce RTX 2080 Ti 11 GB GPU.

## Software requirements
The code has been tested up to commit `02820d339eed6291b6af6ccb809f154ad6244110` on the `master` branch.

### Operating system
This package has been tested on *Linux* Ubuntu 20.04.6 LTS but we expect it to be agnostic to the *Linux* system version.

### Conda environment
[Conda](https://docs.conda.io/projects/conda/en/latest/user-guide/install/linux.html) is required. Either Miniconda or Anaconda should be installed.

Following that, the Python dependencies for the code can be installed with the following command
```
conda env create -f environment.yml
```

Installation might take 10-20 minutes to resolve dependencies.

### Additional software
1. [LAMMPS](https://docs.lammps.org/Install.html) for classical force field optimization
2. [NFF](https://github.com/learningmatter-mit/NeuralForceField) for neural network force field

# Setup
Assuming you have cloned our `surface-sampling` repo to `/path/to/surface-sampling`.

Add the following to `~/.bashrc` or equivalent with appropriate paths and then `source ~/.bashrc`.
```
export SURFSAMPLINGDIR="/path/to/surface-sampling"
export PYTHONPATH="$SURFSAMPLINGDIR:$PYTHONPATH"

export LAMMPS_COMMAND="/path/to/lammps/src/lmp_serial"
export LAMMPS_POTENTIALS="/path/to/lammps/potentials/"
export ASE_LAMMPSRUN_COMMAND="$LAMMPS_COMMAND"

export NFFDIR="/path/to/NeuralForceField"
export PYTHONPATH=$NFFDIR:$PYTHONPATH
```

You might have to re-open/re-login to your shell for the new settings to take effect.

# Demo

A toy demo and other examples can be found in the `tutorials/` folder. More data/examples can be found in our Zenodo dataset (https://doi.org/10.5281/zenodo.7758174).

### Toy example of Cu(100)
A toy example to illustrate the use of VSSR-MC. It should only take about a minute to run. Refer to `tutorials/example.ipynb`.

### GaN(0001) surface sampling with Tersoff potential
We explicitly generate surface sites using `pymatgen`. This example could take 5 minutes or more to run. Refer to `tutorials/GaN_0001.ipynb`.

### Si(111) 5x5 surface sampling with modified Stillinger–Weber potential
We explicitly generate surface sites using `pymatgen`. This example could take 5 minutes or more to run. Refer to `tutorials/Si_111_5x5.ipynb`.

### SrTiO3(001) surface sampling with machine learning potential
Demonstrates the integration of VSSR-MC with a neural network force field. This example could take 10 minutes or more to run. Refer to `tutorials/SrTiO3_001.ipynb`.

### Clustering MC-sampled surfaces in the latent space
Retrieving the neural network embeddings of VSSR-MC structures and performing clustering. This example should only take a minute to run. Refer to `tutorials/latent_space_clustering.ipynb`.
