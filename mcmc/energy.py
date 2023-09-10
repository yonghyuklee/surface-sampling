import copy
import json
import logging
import os
from collections import Counter

import ase
import numpy as np
from ase.optimize import BFGS
from lammps import (
    LMP_STYLE_ATOM,
    LMP_STYLE_GLOBAL,
    LMP_TYPE_SCALAR,
    LMP_TYPE_VECTOR,
    lammps,
)
from nff.io.ase import AtomsBatch
from nff.utils.constants import EV_TO_KCAL_MOL, HARTREE_TO_KCAL_MOL

HARTREE_TO_EV = HARTREE_TO_KCAL_MOL / EV_TO_KCAL_MOL
# threshold for unrelaxed energy
# keep it at 100 eV above expected lowest energy
# UNRELAXED_ENERGY_THRESHOLD = -300  # for Si(111) 5x5 in eV
UNRELAXED_ENERGY_THRESHOLD = 0  # for Si(111) 3x3 in eV
UNRELAXED_MAX_FORCE_THRESHOLD = 1000

logger = logging.getLogger(__name__)
curr_dir = os.getcwd()
OPT_TEMPLATE = open(f"{curr_dir}/lammps_opt_template.txt", "r").read()
ENERGY_TEMPLATE = open(f"{curr_dir}/lammps_energy_template.txt", "r").read()


def run_lammps_calc(slab, main_dir=os.getcwd(), lammps_template=OPT_TEMPLATE, **kwargs):
    curr_dir = os.getcwd()

    # config file is assumed to be stored in the folder you run lammps
    config = json.load(open(f"{curr_dir}/lammps_config.json"))
    potential_file = config["potential_file"]
    atoms = config["atoms"]
    bulk_index = config["bulk_index"]

    # define necessary file locations
    lammps_data_file = f"{main_dir}/lammps.data"
    lammps_in_file = f"{main_dir}/lammps.in"
    lammps_out_file = f"{main_dir}/lammps.out"
    cif_from_lammps_path = f"{main_dir}/lammps.cif"

    # write current surface into lammps.data
    slab.write(
        lammps_data_file, format="lammps-data", units="real", atom_style="atomic"
    )
    # write lammps.in file
    with open(lammps_in_file, "w") as f:
        # if using KIM potential
        if kwargs.get("kim_potential", False):
            f.writelines(
                lammps_template.format(lammps_data_file, bulk_index, lammps_out_file)
            )
        else:
            f.writelines(
                lammps_template.format(
                    lammps_data_file,
                    bulk_index,
                    potential_file,
                    *atoms,
                    lammps_out_file,
                )
            )

    # run LAMMPS without too much output
    lmp = lammps(cmdargs=["-log", "none", "-screen", "none", "-nocite"])
    # lmp = lammps()
    logger.debug(lmp.file(lammps_in_file))

    energy = lmp.extract_compute("thermo_pe", LMP_STYLE_GLOBAL, LMP_TYPE_SCALAR)
    if "opt" in lammps_template:
        pe_per_atom = []
    else:
        pe_per_atom = lmp.extract_compute("pe_per_atom", LMP_STYLE_ATOM, LMP_TYPE_VECTOR)
        pe_per_atom = np.ctypeslib.as_array(
            pe_per_atom, shape=(len(slab),)
        )  # convert to numpy array
    lmp.close()

    # Read from LAMMPS out
    new_slab = ase.io.read(lammps_out_file, format="lammps-data", style="atomic")

    atomic_numbers_dict = config["atomic_numbers_dict"]
    actual_atomic_numbers = [
        atomic_numbers_dict[str(x)] for x in new_slab.get_atomic_numbers()
    ]

    new_slab.set_atomic_numbers(actual_atomic_numbers)
    new_slab.calc = slab.calc

    return energy, pe_per_atom, new_slab


def run_lammps_opt(slab, main_dir=os.getcwd(), **kwargs):
    energy, pe_per_atom, opt_slab = run_lammps_calc(
        slab, main_dir=main_dir, lammps_template=OPT_TEMPLATE, **kwargs
    )
    logger.info(f"slab energy in relaxation: {energy}")
    return opt_slab, energy


def run_lammps_energy(slab, main_dir=os.getcwd(), **kwargs):
    energy, pe_per_atom, _ = run_lammps_calc(
        slab, main_dir=main_dir, lammps_template=ENERGY_TEMPLATE, **kwargs
    )
    # logger.info(f"slab energy in engrad: {energy}")
    return energy, pe_per_atom


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
            calc_slab, energy = run_lammps_opt(slab, main_dir=folder_name, **kwargs)
        else:
            calc_slab, energy = run_lammps_opt(slab, **kwargs)
    else:
        energy = None
        if type(slab) is AtomsBatch:
            slab.update_nbr_list(update_atoms=True)
            calc_slab = copy.deepcopy(slab)
        else:
            calc_slab = slab.copy()
        calc_slab.calc = slab.calc
        if (
            kwargs.get("folder_name", None)
            and kwargs.get("iter", None)
            and kwargs.get("save", False)
        ):
            # save every 10 steps
            iter = int(kwargs.get("iter"))
            # if iter % 10 == 0:
            # save only when told to
            dyn = BFGS(
                calc_slab,
                trajectory=os.path.join(
                    kwargs["folder_name"],
                    f"final_slab_traj_{iter:04}.traj",
                ),
            )
        else:
            dyn = BFGS(calc_slab)

        # default steps is 20 and max forces are 0.01
        # TODO set up a config file to change this
        steps = kwargs.get("relax_steps", 20)
        dyn.run(steps=steps, fmax=0.01)

    if (
        kwargs.get("folder_name", None)
        and kwargs.get("iter", None)
        and kwargs.get("save", False)
    ):
        # save the final frame as cif
        iter = int(kwargs.get("iter"))
        calc_slab.write(
            f"{kwargs['folder_name']}/optim_slab_run_{iter:03}_{calc_slab.get_chemical_formula()}.cif"
        )

    return calc_slab, energy


def slab_energy(slab, relax=False, update_neighbors=True, **kwargs):
    """Calculate slab energy."""

    ENERGY_THRESHOLD = UNRELAXED_ENERGY_THRESHOLD

    MAX_FORCE_THRESHOLD = UNRELAXED_MAX_FORCE_THRESHOLD

    RELAXED_ENERGY_THRESHOLD = ENERGY_THRESHOLD
    energy = 0.0

    pe_per_atom = []

    if relax:
        # calculate without relax first
        # logger.info(f"\ncalculating energy without relax")
        # energy, energy_std, max_force, force_std = slab_energy(
        #     slab, relax=False, **kwargs
        # )

        # if energy > ENERGY_THRESHOLD or max_force > MAX_FORCE_THRESHOLD:
        #     logger.info("encountered energy or forces out of bounds")
        #     logger.info(f"max_force {max_force:.3f}, energy {energy:.3f}")

        #     if kwargs.get("folder_name", None) and kwargs.get("iter", None):
        #         # save the final frame as cif
        #         logger.info("saving this slab")
        #         iter = int(kwargs.get("iter"))
        #         slab.write(
        #             f"{kwargs['folder_name']}/oob_trial_slab_run_{energy:.3f}_{max_force:.3f}_{iter:03}_{slab.get_chemical_formula()}.cif"
        #         )

        #     # TODO: save the trajectory
        #     # optimize_slab(slab, **kwargs)

        #     # these energies or forces are out of bounds, thus
        #     # we set a high energy for mcmc to reject
        #     energy = ENERGY_THRESHOLD
        #     # energy = np.sign(energy) * UNRELAXED_ENERGY_THRESHOLD

        #     return energy, energy_std, max_force, force_std
        logger.info(f"performing relaxation")
        slab, energy = optimize_slab(slab, **kwargs)

    if kwargs.get("require_per_atom_energies", False):
        _, pe_per_atom = run_lammps_energy(
            slab, main_dir=kwargs.get("folder_name", None), **kwargs
        )

    if type(slab) is AtomsBatch:
        if update_neighbors:
            slab.update_nbr_list(update_atoms=True)
        slab.calc.calculate(slab)
        energy = float(slab.results["energy"])
        max_force = float(np.abs(slab.results["forces"]).max())

        if np.abs(energy) > ENERGY_THRESHOLD or max_force > MAX_FORCE_THRESHOLD:
            logger.info("encountered energy out of bounds")
            logger.info(f"energy {energy:.3f}")

            # we set a high energy for mcmc to reject
            energy = ENERGY_THRESHOLD

        if kwargs.get("offset", None):
            if not kwargs.get("offset_data", None):
                raise Exception(f"No offset_data.json file specified!")
            else:
                with open(kwargs["offset_data"]) as f:
                    offset_data = json.load(f)
                bulk_energies = offset_data["bulk_energies"]
                stoidict = offset_data["stoidict"]
                stoics = offset_data["stoics"]
                ref_formula = offset_data["ref_formula"]
                ref_element = offset_data["ref_element"]

            ad = Counter(slab.get_chemical_symbols())

            # procedure is
            # 1: to add the linear regression coeffs back in
            ref_en = 0
            for ele, num in ad.items():
                ref_en += num * stoidict[ele]
            ref_en += stoidict["offset"]

            energy += ref_en * HARTREE_TO_EV

            # 2: subtract the bulk energies
            bulk_ref_en = ad[ref_element] * bulk_energies[ref_formula]
            for ele, _ in ad.items():
                if ele != ref_element:
                    bulk_ref_en += (
                        ad[ele] - stoics[ele] / stoics[ref_element] * ad[ref_element]
                    ) * bulk_energies[ele]

            energy -= bulk_ref_en * HARTREE_TO_EV

        energy_std = float(slab.results["energy_std"])
        max_force = float(np.abs(slab.results["forces"]).max())
        force_std = float(slab.results["forces_std"].mean())

    elif kwargs.get("optimizer", None) == "LAMMPS":
        # BAD Code i know
        # energy would have already been calculated
        # folder_name = kwargs.get("folder_name", None)
        # energy = run_lammps_energy(slab, main_dir=folder_name, **kwargs)
        energy_std = 0.0
        max_force = 0.0
        force_std = 0.0

    else:
        energy = float(slab.get_potential_energy())
        energy_std = 0.0
        max_force = 0.0
        force_std = 0.0
    return energy, energy_std, max_force, force_std, pe_per_atom
