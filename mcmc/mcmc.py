"""Performs Semi-Grand Monte Carlo (SGMC) reconstruction of a surface.
Produces a temperature/structure map
"""

import copy
import os
import sys

from nff.io.ase import AtomsBatch

sys.path.append("/home/dux/")
import cProfile
import logging
from collections import Counter, defaultdict
from datetime import datetime
from pstats import Stats
from time import perf_counter

import ase
import catkit
import matplotlib.pyplot as plt
import numpy as np
from ase.calculators.eam import EAM
from ase.calculators.lammpsrun import LAMMPS
from ase.constraints import FixAtoms
from ase.io import read, write
from catkit.gen.adsorption import get_adsorption_sites

from .energy import optimize_slab, slab_energy
from .plot import plot_summary_stats
from .slab import (
    change_site,
    count_adsorption_sites,
    get_adsorption_coords,
    get_complementary_idx,
    get_random_idx,
    initialize_slab,
)
from .utils import filter_distances_new

# from htvs.djangochem.pgmols.utils import surfaces

logger = logging.getLogger(__name__)
file_dir = os.path.dirname(__file__)


def spin_flip_canonical(
    state,
    slab,
    temp,
    coords,
    connectivity,
    prev_energy=None,
    save_cif=False,
    iter=1,
    testing=False,
    folder_name=".",
    adsorbates=["Cu"],
    relax=False,
    filter_distance=0,
):
    """Based on the Ising model, models the adsorption/desorption of atoms from surface lattice sites.
    Uses canonical ensemble, fixed number of atoms.

    Parameters
    ----------
    state : np.array
        dimension the number of sites
    slab : catkit.gratoms.Gratoms
        model of the surface slab
    temp : float
        temperature

    Returns
    -------
    np.array, float
        new state, energy change
    """
    if type(adsorbates) == str:
        adsorbates = list([adsorbates])

    if not prev_energy and not testing:
        # calculate energy of current state
        prev_energy = slab_energy(slab, relax=relax, folder_name=folder_name)

    # choose 2 sites of different ads (empty counts too) to flip
    site1_idx, site2_idx, site1_ads, site2_ads = get_complementary_idx(state, slab=slab)

    # fake pots
    pots = list(range(len(adsorbates)))

    site1_coords = coords[site1_idx]
    site2_coords = coords[site2_idx]

    logger.debug(f"\n we are at iter {iter}")
    logger.debug(
        f"idx is {site1_idx} with connectivity {connectivity[site1_idx]} at {site1_coords}"
    )
    logger.debug(
        f"idx is {site2_idx} with connectivity {connectivity[site2_idx]} at {site2_coords}"
    )

    logger.debug(f"before proposed state is")
    logger.debug(state)

    logger.debug(f"current slab has {len(slab)} atoms")

    # effectively switch ads at both sites
    slab, state, _, _, _ = change_site(
        slab,
        state,
        pots,
        adsorbates,
        coords,
        site1_idx,
        start_ads=site1_ads,
        end_ads=site2_ads,
    )
    slab, state, _, _, _ = change_site(
        slab,
        state,
        pots,
        adsorbates,
        coords,
        site2_idx,
        start_ads=site2_ads,
        end_ads=site1_ads,
    )

    # make sure num atoms is conserved
    logger.debug(f"proposed slab has {len(slab)} atoms")

    logger.debug(f"after proposed state is")
    logger.debug(state)

    if save_cif:
        if not os.path.exists(folder_name):
            os.makedirs(folder_name)
        write(f"{folder_name}/proposed_slab_iter_{iter:03}.cif", slab)

    # to test, always accept
    accept = False

    if filter_distance:
        energy = 0

        if filter_distances_new(slab, ads=adsorbates, cutoff_distance=filter_distance):
            # succeeds! keep already changed slab
            logger.debug("state changed with filtering!")
            accept = True
        else:
            # failed, keep current state and revert slab back to original
            slab, state, _, _, _ = change_site(
                slab,
                state,
                pots,
                adsorbates,
                coords,
                site1_idx,
                start_ads=site2_ads,
                end_ads=site1_ads,
            )
            slab, state, _, _, _ = change_site(
                slab,
                state,
                pots,
                adsorbates,
                coords,
                site2_idx,
                start_ads=site1_ads,
                end_ads=site2_ads,
            )

            logger.debug("state kept the same with filtering")

    elif testing:
        # state = state.copy() # obviously inefficient but here for a reason
        energy = 0
    else:
        # use relaxation only to get lowest energy
        # but don't update adsorption positions
        curr_energy = slab_energy(slab, relax=relax, folder_name=folder_name)

        logger.debug(f"prev energy is {prev_energy}")
        logger.debug(f"curr energy is {curr_energy}")

        # energy change due to flipping spin
        energy_diff = curr_energy - prev_energy

        # check if transition succeeds
        logger.debug(f"energy diff is {energy_diff}")
        logger.debug(f"k_b T {temp}")
        base_prob = np.exp(-energy_diff / temp)
        logger.debug(f"base probability is {base_prob}")

        if np.random.rand() < base_prob:
            # succeeds! keep already changed slab
            # state = state.copy()
            logger.debug("state changed!")
            energy = curr_energy
            accept = True
        else:
            # failed, keep current state and revert slab back to original
            slab, state, _, _, _ = change_site(
                slab,
                state,
                pots,
                adsorbates,
                coords,
                site1_idx,
                start_ads=site2_ads,
                end_ads=site1_ads,
            )
            slab, state, _, _, _ = change_site(
                slab,
                state,
                pots,
                adsorbates,
                coords,
                site2_idx,
                start_ads=site1_ads,
                end_ads=site2_ads,
            )

            # state, slab = add_to_slab(slab, state, adsorbate, coords, site1_idx)
            # state, slab = remove_from_slab(slab, state, site2_idx)

            logger.debug("state kept the same")
            energy = prev_energy
            accept = False

    return state, slab, energy, accept


def get_SrTiO3_exponent(slab_energy, bulk_energies, delta_mus, ads_counts):
    pass


def spin_flip(
    state,
    slab,
    temp,
    pots,
    coords,
    connectivity,
    prev_energy=None,
    save_cif=False,
    iter=1,
    site_idx=None,
    testing=False,
    folder_name=".",
    adsorbates=["Cu"],
    relax=False,
    filter_distance=0,
):

    """It takes in a slab, a state, and a temperature, and it randomly chooses a site to flip. If the site
    is empty, it adds an atom to the slab and updates the state. If the site is filled, it removes an
    atom from the slab and updates the state. It then calculates the energy of the new slab and compares
    it to the energy of the old slab. If the new energy is lower, it accepts the change. If the new
    energy is higher, it accepts the change with a probability that depends on the temperature

    Parameters
    ----------
    state
        the current state of the adsorption sites, which is a list of the corresponding indices in the slab.
    slab
        the slab object
    temp
        the temperature of the system
    pot
        the chemical potential of the adsorbate
    coords
        the coordinates of the adsorption sites
    connectivity
        a list of lists, where each list is the indices of the sites that are connected to the site at the
    same index.
    prev_energy
        the energy of the slab before the spin flip
    save_cif, optional
        if True, will save the proposed slab to a cif file
    iter, optional
        the iteration number
    site_idx
        the index of the site to switch
    testing, optional
        if True, always accept the proposed state
    folder_name, optional
        the folder where the cif files will be saved
    adsorbate, optional
        the type of atom to adsorb
    relax, optional
        whether to relax the slab after adsorption

    Returns
    -------
        state, slab, energy, accept

    """

    # choose a site to flip
    # coords, connectivity, sym_idx = get_adsorption_sites(slab, symmetry_reduced=False)

    # change int pot and adsorbate to list
    if type(pots) == int:
        pots = list([pots])
    if type(adsorbates) == str:
        adsorbates = list([adsorbates])

    if not site_idx:
        site_idx = get_random_idx(connectivity)
    rand_site = coords[site_idx]

    logger.debug(f"\n we are at iter {iter}")
    logger.debug(
        f"idx is {site_idx} with connectivity {connectivity[site_idx]} at {rand_site}"
    )

    # determine if site vacant or filled
    # filled = (state > 0)[site_idx]
    logger.debug(f"before proposed state is")
    logger.debug(state)

    # change in number of adsorbates (atoms)
    delta_N = 0

    if not prev_energy and not testing:
        prev_energy = slab_energy(slab, relax=relax, folder_name=folder_name)

    slab, state, delta_pot, start_ads, end_ads = change_site(
        slab, state, pots, adsorbates, coords, site_idx, start_ads=None, end_ads=None
    )

    logger.debug(f"after proposed state is")
    logger.debug(state)

    if save_cif:
        if not os.path.exists(folder_name):
            os.makedirs(folder_name)
        write(f"{folder_name}/proposed_slab_iter_{iter:03}.cif", slab)

    # to test, always accept
    accept = False
    if filter_distance:
        energy = 0

        if filter_distances_new(slab, ads=adsorbates, cutoff_distance=filter_distance):
            # succeeds! keep already changed slab
            logger.debug("state changed with filtering!")
            accept = True
        else:
            # failed, keep current state and revert slab back to original
            slab, state, _, _, _ = change_site(
                slab,
                state,
                pots,
                adsorbates,
                coords,
                site_idx,
                start_ads=end_ads,
                end_ads=start_ads,
            )
            logger.debug("state kept the same with filtering")

    elif testing:
        energy = 0
        accept = True

    else:
        # use relaxation only to get lowest energy
        # but don't update adsorption positions
        curr_energy = slab_energy(slab, relax=relax, folder_name=folder_name)

        logger.debug(f"prev energy is {prev_energy}")
        logger.debug(f"curr energy is {curr_energy}")

        # energy change due to flipping spin
        energy_diff = curr_energy - prev_energy

        # check if transition succeeds
        # min(1, exp(-(\delta_E-(delta_N*pot))))
        logger.debug(f"energy diff is {energy_diff}")
        logger.debug(f"chem pot(s) is(are) {pots}")
        logger.debug(f"delta_N {delta_N}")
        logger.debug(f"k_b T {temp}")
        base_prob = np.exp(-(energy_diff - delta_pot) / temp)
        logger.debug(f"base probability is {base_prob}")

        if np.random.rand() < base_prob:
            # succeeds! keep already changed slab
            # state = state.copy()
            logger.debug("state changed!")
            energy = curr_energy
            accept = True
        else:
            # failed, keep current state and revert slab back to original
            slab, state, _, _, _ = change_site(
                slab,
                state,
                pots,
                adsorbates,
                coords,
                site_idx,
                start_ads=end_ads,
                end_ads=start_ads,
            )

            logger.debug("state kept the same")
            energy = prev_energy
            accept = False

    return state, slab, energy, accept


def mcmc_run(
    num_sweeps=1000,
    temp=1,
    pot=1,
    alpha=0.9,
    slab=None,
    calc=EAM(
        potential=os.path.join(
            os.path.dirname(os.path.realpath(__file__)), "potentials", "Cu2.eam.fs"
        )
    ),
    surface_name=None,
    element="Cu",
    canonical=False,
    num_ads_atoms=0,
    ads_coords=[],
    testing=False,
    adsorbates=None,
    relax=False,
    filter_distance=0.0,
):
    """Performs MCMC sweep with given parameters, initializing with a random slab if not given an input.
    Each sweep is defined as running a number of trials equal to the number adsorption sites. Each trial
    consists of randomly picking a site and proposing (and accept/reject) a flip (adsorption or desorption).
    Only the resulting slab after one sweep is appended to the history. Corresponding observables are
    calculated also after each run.

    Parameters
    ----------
    num_sweeps, optional
        number of runs to perform
    temp, optional
        temperature of the system
    pot, optional
        the chemical potential of the adsorption site.
    alpha
        the simulated annealing schedule
    slab
        the initial slab to start with. If None, will initialize a slab.
    calc
        ase calculator object for the slab
    surface_name
        name of the surface, e.g. Cu
    element, optional
        the element of the slab
    canonical, optional
        whether to perform canonical runs or not
    num_ads_atoms, optional
        number of adsorbed atoms if performing canonical runs
    ads_coords
        list of adsorption coordinates
    testing, optional
        if True, will accept all moves
    adsorbates : ase.Atoms
        the element to adsorb
    relax, optional
        whether to relax the slab after adsorption

    Returns
    -------
        history is a list of slab objects, energy_hist is a list of energies, frac_accept_hist is a list of
    fraction of accepted moves, adsorption_count_hist is a dictionary of lists of adsorption counts for
    each site type

    """

    logging.basicConfig(
        format="%(levelname)s:%(message)s",
        level=logging.INFO,
        datefmt="%m/%d/%Y %I:%M:%S %p",
    )

    logger.info(
        f"Running with num_sweeps = {num_sweeps}, temp = {temp}, pot = {pot}, alpha = {alpha}"
    )
    # Cu lattice at 293 K, 3.6147 Å, potential ranges from 0 - 2
    if (
        (type(slab) is not catkit.gratoms.Gratoms)
        and (type(slab) is not ase.atoms.Atoms)
        and (type(slab) is not AtomsBatch)
    ):
        # initialize slab
        logger.info("initializing slab")
        # Cu alat from https://www.copper.org/resources/properties/atomic_properties.html
        Cu_alat = 3.6147
        slab = initialize_slab(Cu_alat)

    # attach slab calculator
    slab.calc = calc

    num_bulk_atoms = len(slab)
    bulk_indices = list(range(num_bulk_atoms))

    c = FixAtoms(indices=bulk_indices)
    slab.set_constraint(c)

    pristine_atoms = len(slab)

    logger.info(f"there are {pristine_atoms} atoms ")
    logger.info(f"using slab calc {slab.calc}")

    # get absolute adsorption coords
    metal = catkit.gratoms.Gratoms(element)

    # set surface_name
    if not surface_name:
        surface_name = element

    if not (
        (isinstance(ads_coords, list) and (len(ads_coords) > 0))
        or isinstance(ads_coords, np.ndarray)
    ):
        # get ALL the adsorption sites
        # top should have connectivity 1, bridge should be 2 and hollow more like 4
        coords, connectivity, sym_idx = get_adsorption_sites(
            slab, symmetry_reduced=False
        )
        ads_coords = get_adsorption_coords(slab, metal, connectivity, debug=True)
    else:
        # fake connectivity
        connectivity = np.ones(len(ads_coords), dtype=int)

    # state of each vacancy in slab. for state > 0, it's filled, and that's the index of the adsorbate atom in slab
    state = np.zeros(len(ads_coords), dtype=int)

    logger.info(f"In pristine slab, there are a total of {len(ads_coords)} sites")

    # sometimes slab.calc is fake
    if slab.calc:
        energy = slab_energy(slab)
    else:
        energy = 0

    start_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    run_folder = os.path.join(
        os.getcwd(),
        f"{surface_name}/runs{num_sweeps}_temp{temp}_pot{pot}_alpha{alpha}_{start_timestamp}",
    )

    # initialize tags
    # set tags; 1 for surface atoms, 2 for adsorbates, 0 for others
    if type(slab) is catkit.gratoms.Gratoms:
        surface_atoms = slab.get_surface_atoms()
        atoms_arr = np.arange(0, len(slab))
        base_tags = np.int8(np.isin(atoms_arr, surface_atoms)).tolist()
        slab.set_tags(list(base_tags))

    # set adsorbate
    if not adsorbates:
        adsorbates = element
    logger.info(f"adsorbate(s) is(are) {adsorbates}")

    if canonical:
        # perform canonical runs
        # adsorb num_ads_atoms
        assert (
            num_ads_atoms > 0
        ), "for canonical runs, need number of adsorbed atoms greater than 0"

        # following doesn't work because of infinite energies
        # for i in range(num_ads_atoms):
        # site_idx = get_random_idx(connectivity)
        # state, slab = add_to_slab(slab, state, element, ads_coords, site_idx)

        # customize run folder
        run_folder = os.path.join(
            os.getcwd(),
            f"{surface_name}/runs{num_sweeps}_temp{temp}_adsatoms{num_ads_atoms:02}_alpha{alpha}_{start_timestamp}",
        )
        if not os.path.exists(run_folder):
            os.makedirs(run_folder)

        # fake pots
        pots = list(range(len(adsorbates)))

        # perform grand canonical until num_ads_atoms are obtained
        while len(slab) < pristine_atoms + num_ads_atoms:
            state, slab, energy, accept = spin_flip(
                state,
                slab,
                temp,
                pots,
                ads_coords,
                connectivity,
                prev_energy=energy,
                save_cif=False,
                testing=testing,
                folder_name=run_folder,
                adsorbates=adsorbates,
                relax=relax,
                filter_distance=filter_distance,
            )

        slab.write(f"{surface_name}_canonical_init.cif")

    if not os.path.exists(run_folder):
        os.makedirs(run_folder)

    history = []
    energy_hist = np.random.rand(num_sweeps)
    # energy_sq_hist = np.random.rand(num_sweeps)
    adsorption_count_hist = defaultdict(list)
    frac_accept_hist = np.random.rand(num_sweeps)

    # sweep over # sites
    sweep_size = len(ads_coords)

    logger.info(
        f"running for {sweep_size} iterations per run over a total of {num_sweeps} runs"
    )

    site_types = set(connectivity)

    for i in range(num_sweeps):
        num_accept = 0
        # simulated annealing schedule
        curr_temp = temp * alpha**i
        logger.info(f"In sweep {i+1} out of {num_sweeps}")
        for j in range(sweep_size):
            # logger.info(f"In iter {j+1}")
            run_idx = sweep_size * i + j + 1
            if canonical:
                state, slab, energy, accept = spin_flip_canonical(
                    state,
                    slab,
                    curr_temp,
                    ads_coords,
                    connectivity,
                    prev_energy=energy,
                    save_cif=False,
                    iter=run_idx,
                    testing=testing,
                    folder_name=run_folder,
                    adsorbates=adsorbates,
                    relax=relax,
                    filter_distance=filter_distance,
                )
            else:
                state, slab, energy, accept = spin_flip(
                    state,
                    slab,
                    curr_temp,
                    pot,
                    ads_coords,
                    connectivity,
                    prev_energy=energy,
                    save_cif=False,
                    iter=run_idx,
                    testing=testing,
                    folder_name=run_folder,
                    adsorbates=adsorbates,
                    relax=relax,
                    filter_distance=filter_distance,
                )
            num_accept += accept

        # end of sweep; append to history
        slab_copy = copy.deepcopy(slab)
        slab_copy.calc = None
        history.append(slab_copy)

        if relax:
            opt_slab = optimize_slab(slab, folder_name=run_folder)
            opt_slab.write(f"{run_folder}/optim_slab_run_{i+1:03}.cif")

        if type(slab) is AtomsBatch:
            # add in uncertainty information
            slab.update_nbr_list(update_atoms=True)
            slab.calc.calculate(slab)
            # slab_energy(slab)

            if not set(["O", "Sr", "Ti"]) ^ set(adsorbates):
                ads_count = Counter(slab.get_chemical_symbols())
                ads_pot_dict = dict(zip(adsorbates, pot))
                delta_pot = (ads_count["O"] - 3 * ads_count["Ti"]) * ads_pot_dict[
                    "O"
                ] + (ads_count["Sr"] - ads_count["Ti"]) * ads_pot_dict["Sr"]
                energy = float(slab.results["energy"]) - delta_pot
                logger.info(
                    f"optim structure has Free Energy = {energy:.3f}+/-{float(slab.results['energy_std']):.3f}"
                )
            else:
                energy = float(slab.results["energy"])
                logger.info(
                    f"optim structure has Energy = {energy:.3f}+/-{float(slab.results['energy_std']):.3f}"
                )

            # save cif file
            write(
                f"{run_folder}/final_slab_run_{i+1:03}_{energy:.3f}+/-{float(slab.results['energy_std']):.3f}.cif",
                slab,
            )

        else:
            logger.info(f"optim structure has Energy = {energy}")

            # save cif file
            write(f"{run_folder}/final_slab_run_{i+1:03}_{energy:.3f}.cif", slab)

        # append values
        energy_hist[i] = energy
        # energy_sq_hist[i] = energy**2

        ads_counts = count_adsorption_sites(slab, state, connectivity)
        for key in set(site_types):
            if ads_counts[key]:
                adsorption_count_hist[key].append(ads_counts[key])
            else:
                adsorption_count_hist[key].append(0)

        frac_accept = num_accept / sweep_size
        frac_accept_hist[i] = frac_accept

    # plot and save the results
    plot_summary_stats(
        energy_hist, frac_accept_hist, adsorption_count_hist, num_sweeps, run_folder
    )

    # early stopping
    # if i > 0 and abs(energy_hist[i-1] - energy_hist[i]) < 1e-2:
    #     return history, energy_hist, frac_accept_hist, adsorption_count_hist

    return history, energy_hist, frac_accept_hist, adsorption_count_hist, run_folder


if __name__ == "__main__":
    logging.basicConfig(
        format="%(levelname)s:%(message)s",
        level=logging.DEBUG,
        datefmt="%m/%d/%Y %I:%M:%S %p",
    )

    do_profiling = True

    # use EAM
    # eam_calc = EAM(potential='Cu2.eam.fs')

    # use LAMMPS
    alloy_parameters = {
        "pair_style": "eam/alloy",
        "pair_coeff": ["* * cu_ag_ymwu.eam.alloy Ag"],
    }
    alloy_potential_file = os.path.join(
        os.path.dirname(__file__), "cu_ag_ymwu.eam.alloy"
    )
    alloy_calc = LAMMPS(
        files=[alloy_potential_file],
        keep_tmp_files=False,
        keep_alive=False,
        tmp_dir="/home/dux/surface_sampling/tmp_files",
    )
    alloy_calc.set(**alloy_parameters)

    # Au from standard cell
    atoms = read("Ag_mp-124_conventional_standard.cif")
    # slab, surface_atoms = surfaces.surface_from_bulk(atoms, [1, 1, 1], size=[5, 5])
    # TODO: fix this
    slab.write("Ag_111_5x5_pristine_slab.cif")

    element = "Ag"
    # num_ads_atoms = 16 + 8
    adsorbate = "Cu"
    if do_profiling:
        with cProfile.Profile() as pr:
            start = perf_counter()
            # chem pot 0 to less complicate things
            # temp in terms of kbT
            # history, energy_hist, frac_accept_hist, adsorption_count_hist = mcmc_run(num_sweeps=10, temp=1, pot=0, slab=slab, calc=lammps_calc, element=element, canonical=True, num_ads_atoms=num_ads_atoms)
            history, energy_hist, frac_accept_hist, adsorption_count_hist = mcmc_run(
                num_sweeps=1,
                temp=1,
                pot=0,
                alpha=0.99,
                slab=slab,
                calc=alloy_calc,
                element=element,
                adsorbates=adsorbate,
            )
            stop = perf_counter()
            logger.info(f"Time taken = {stop - start} seconds")

        with open("profiling_stats.txt", "w") as stream:
            stats = Stats(pr, stream=stream)
            stats.strip_dirs()
            stats.sort_stats("time")
            stats.dump_stats(".prof_stats")
            stats.print_stats()