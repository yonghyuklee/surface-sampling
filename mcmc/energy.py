import copy
import json
import logging
import os
from collections import Counter

from ase import io
from ase.optimize import BFGS
from lammps import lammps
from nff.io.ase import AtomsBatch
from nff.utils.constants import HARTREE_TO_EV

logger = logging.getLogger(__name__)


def run_lammps_opt(slab, main_dir=os.getcwd()):
    curr_dir = os.getcwd()

    # config file is assumed to be stored in the folder you run lammps
    config = json.load(open(f"{curr_dir}/lammps_config.json"))
    potential_file = config["potential_file"]
    atoms = config["atoms"]

    # define necessary file locations
    lammps_data_file = f"{main_dir}/lammps.data"
    lammps_in_file = f"{main_dir}/lammps.in"
    lammps_out_file = f"{main_dir}/lammps.out"
    cif_from_lammps_path = f"{main_dir}/lammps.cif"

    # write current surface into lammps.data
    slab.write(
        lammps_data_file, format="lammps-data", units="real", atom_style="atomic"
    )

    TEMPLATE = open(f"{curr_dir}/lammps_template.txt", "r").read()
    # write lammps.in file
    with open(lammps_in_file, "w") as f:
        f.writelines(
            TEMPLATE.format(lammps_data_file, potential_file, *atoms, lammps_out_file)
        )

    lmp = lammps()

    # run the LAMMPS here
    logger.debug(lmp.file(lammps_in_file))
    lmp.close()

    # Read from LAMMPS out
    opt_slab = io.read(lammps_out_file, format="lammps-data", style="atomic")

    atomic_numbers_dict = config["atomic_numbers_dict"]
    actual_atomic_numbers = [
        atomic_numbers_dict[str(x)] for x in opt_slab.get_atomic_numbers()
    ]

    opt_slab.set_atomic_numbers(actual_atomic_numbers)
    opt_slab.calc = slab.calc

    return opt_slab


def optimize_slab(slab, optimizer="BFGS", **kwargs):
    """Run relaxation for slab

    Parameters
    ----------
    slab : ase.Atoms
        Surface slab
    optimizer : str, optional
        Either  BFGS or LAMMPS, by default 'BFGS'

    Returns
    -------
    ase.Atoms
        Relaxed slab
    """
    if "LAMMPS" in optimizer:
        if "folder_name" in kwargs:
            folder_name = kwargs["folder_name"]
            calc_slab = run_lammps_opt(slab, main_dir=folder_name)
        else:
            calc_slab = run_lammps_opt(slab)
    else:
        if type(slab) is AtomsBatch:
            slab.update_nbr_list(update_atoms=True)
        calc_slab = copy.deepcopy(slab)
        calc_slab.calc = slab.calc
        dyn = BFGS(calc_slab)
        dyn.run(steps=20, fmax=0.2)

    return calc_slab


def slab_energy(slab, relax=False, **kwargs):
    """Calculate slab energy."""

    if relax:
        slab = optimize_slab(slab, **kwargs)

    if type(slab) is AtomsBatch:
        slab.update_nbr_list(update_atoms=True)
        slab.calc.calculate(slab)
        energy = float(slab.results["energy"])
    else:
        energy = float(slab.get_potential_energy())

    return energy
