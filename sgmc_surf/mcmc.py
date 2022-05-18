"""Performs Semi-Grand Monte Carlo (SGMC) reconstruction of a surface.
Produces a temperature/structure map
"""

import os
import socket
hostname = socket.gethostname()

if "kohn" in hostname:
    os.environ["LAMMPS_COMMAND"] = "/home/jurgis/lammps/src/lmp_serial"
    os.environ["LAMMPS_POTENTIALS"] = "/home/jurgis/lammps/potentials/"
elif "lambda" in hostname:
    os.environ["LAMMPS_COMMAND"] = "/home/pleon/mylammps/src/lmp_serial"
    os.environ["LAMMPS_POTENTIALS"] = "/home/pleon/mylammps/potentials/"
elif "hartree" in hostname:
    os.environ["LAMMPS_COMMAND"] = "/home/dux/lammps/src/lmp_serial"
    os.environ["LAMMPS_POTENTIALS"] = "/home/dux/lammps/potentials/"

os.environ["ASE_LAMMPSRUN_COMMAND"] = os.environ["LAMMPS_COMMAND"]
os.environ["PROJECT_DIR"] = os.getcwd()

import sys
sys.path.append("/home/dux/")
from htvs.djangochem.pgmols.utils import surfaces

import matplotlib.pyplot as plt
from time import perf_counter

import cProfile
from pstats import Stats, SortKey
import numpy as np

from ase import io
from ase.spacegroup import crystal
from ase.build import make_supercell, bulk
from ase.io import read, write
from ase.calculators.eam import EAM
from ase.calculators.lammpsrun import LAMMPS
from ase.calculators.lammpslib import LAMMPSlib

from ase.constraints import FixAtoms
from ase.optimize import BFGS, GPMin

from lammps import lammps

import ase
import catkit
from catkit.gen.adsorption import get_adsorption_sites
import random
from collections import Counter, defaultdict

from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def initialize_slab(alat, elem='Cu', vacuum=15.0, miller=(1,0,0), termination=0, orthogonal=False, **kwargs):
    """Creates the slab structure using ASE.

    Parameters
    ----------
    alat : float
        Lattice parameter in angstroms
    """
    # slab = fcc100(elem, size=(4,4,4), a=alat, vacuum=vacuum)

    # TODO: adjust size of surface if necessary
    a1 = bulk(elem, 'fcc', a=alat)
    write(f'{elem}_a1_bulk.cif', a1)
    catkit_slab = catkit.build.surface(a1, size=(4,4,4), miller=miller, termination=termination, fixed=0, vacuum=vacuum, orthogonal=orthogonal, **kwargs)

    write(f'{elem}_pristine_slab.cif', catkit_slab)
    return catkit_slab


def get_random_idx(connectivity, type=None):
    """Get random site index
    """
    connectivities = {
        'top': 1,
        'bridge': 2,
        'hollow': 4
    } # defaults to hollow

    # top should have connectivity 1, bridge should be 2 and hollow more like 4
    if type:
        site_idx = random.choice(np.argwhere(connectivity == connectivities[type]).flatten())

    else:
        site_idx = random.randrange(len(connectivity))

    return site_idx


def get_complementary_idx(state, type=None):
    """Get two indices, site1 occupied and site2 unoccupied."""

    site1_idx = random.choice(np.argwhere(state != 0).flatten())
    site2_idx = random.choice(np.argwhere(state == 0).flatten())

    return site1_idx, site2_idx


def run_lammps_opt(slab):
    # TESTING OUT FOR NOW, WILL FIX LATER
    # TODO: change the data folders to dynamics

    # write lammps data file
    lammps_data_file = "/home/dux/surface_sampling/sgmc_surf/py_lammps/lammps.data"
    lammps_in_file = "/home/dux/surface_sampling/sgmc_surf/py_lammps/lammps.in"
    potential_file = "/home/dux/surface_sampling/sgmc_surf/py_lammps/GaN.tersoff"
    atoms = ["Ga", "N"]
    lammps_out_file = "/home/dux/surface_sampling/sgmc_surf/py_lammps/lammps.out"
    cif_from_lammps_path = "/home/dux/surface_sampling/sgmc_surf/py_lammps/lammps.cif"

    # write current surface into lammps.data
    slab.write(lammps_data_file, format="lammps-data", units="real", atom_style="atomic")

    TEMPLATE = """
clear
atom_style atomic 
units metal
boundary p p p 
atom_modify sort 0 0.0 

# read_data /path/to/data.data
read_data {}

### set bulk
group bulk id <= 36

### interactions
pair_style tersoff 
# pair_coeff * * /path/to/potential Atom1 Atom2
pair_coeff * * {} {} {} 
mass 1 69.723000 
mass 2 14.007000 

### run
reset_timestep 0
fix 2 bulk setforce 0.0 0.0 0.0
thermo 10 # output thermodynamic variables every N timesteps

thermo_style custom step temp press cpu pxx pyy pzz pxy pxz pyz ke pe etotal vol lx ly lz atoms
thermo_modify flush yes format float %23.16g
min_style cg 
minimize 1e-5 1e-5 500 10000

# write_data /path/to/data.out
write_data {}
print "_end of energy minimization_" 
log /dev/stdout

"""

    # write lammps.in file
    with open(lammps_in_file, 'w') as f:
        f.writelines(TEMPLATE.format(lammps_data_file, potential_file, *atoms, lammps_out_file))
    
    lmp = lammps()
    # print("LAMMPS Version: ", lmp.version())

    # run the LAMMPS here
    logger.debug(lmp.file(lammps_in_file))
    lmp.close()

    # Read from LAMMPS out
    opt_slab = io.read(lammps_out_file, format='lammps-data', style='atomic')

    atomic_numbers_dict = {1: 31, 2: 7} # 1: Ga, 2: N
    actual_atomic_numbers = [atomic_numbers_dict[x] for x in opt_slab.get_atomic_numbers()]
    print(f"actual atomic numbers {actual_atomic_numbers}")
    # correct_lammps = new_slab.copy()
    opt_slab.set_atomic_numbers(actual_atomic_numbers)

    opt_slab.calc = slab.calc

    return opt_slab


def optimize_slab(slab, optimizer='LAMMPS'):
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
    if 'LAMMPS' in optimizer:
        calc_slab = run_lammps_opt(slab)

    else:
        calc_slab = slab.copy()
        calc_slab.calc = slab.calc
        dyn = BFGS(calc_slab)
        # dyn = GPMin(calc_slab)
        dyn.run(steps=20,fmax=0.2)

    return calc_slab

def slab_energy(slab, relax=False):
    """Calculate slab energy.
    """

    if relax:
        slab = optimize_slab(slab)

    return slab.get_potential_energy()


def spin_flip_canonical(state, slab, temp, coords, connectivity, prev_energy=None, save_cif=False, iter=1, testing=False, folder_name=".", adsorbate='Cu', relax=False):
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

    # choose 2 complementary sites to flip
    # site1 occupied, site2 unoccupied
    site1_idx, site2_idx = get_complementary_idx(state)

    # import pdb; pdb.set_trace()

    site1_coords = coords[site1_idx]
    site2_coords = coords[site2_idx]

    logger.debug(f"\n we are at iter {iter}")
    logger.debug(f"idx is {site1_idx} with connectivity {connectivity[site1_idx]} at {site1_coords}")
    logger.debug(f"idx is {site2_idx} with connectivity {connectivity[site2_idx]} at {site2_coords}")

    logger.debug(f"before proposed state is")
    logger.debug(state)

    logger.debug(f"current slab has {len(slab)} atoms")

    state, slab = remove_from_slab(slab, state, site1_idx) # desorb from site1
    state, slab = add_to_slab(slab, state, adsorbate, coords, site2_idx) # adsorb to site2

    # import pdb; pdb.set_trace()

    # make sure num atoms is conserved
    logger.debug(f"proposed slab has {len(slab)} atoms")
    
    logger.debug(f"after proposed state is")
    logger.debug(state)

    if save_cif:
        if not os.path.exists(folder_name):
            os.makedirs(folder_name)
        write(f'{folder_name}/proposed_slab_iter_{iter:03}.cif', slab)

    # to test, always accept
    accept = False
    if testing:
        # state = state.copy() # obviously inefficient but here for a reason
        energy = 0
    else:
        if not prev_energy:
            # calculate energy of current state
            if relax:
                prev_energy = slab_energy(slab, relax=True)
            else:
                # calculate energy of current state
                prev_energy = slab_energy(slab)

        if relax:
            # use relaxation only to get lowest energy
            # but don't update adsorption positions
            curr_energy = slab_energy(slab, relax=True)
        else:
            curr_energy = slab_energy(slab)

        logger.debug(f"prev energy is {prev_energy}")
        logger.debug(f"curr energy is {curr_energy}")

        # energy change due to flipping spin
        energy_diff = curr_energy - prev_energy

        # check if transition succeeds
        logger.debug(f"energy diff is {energy_diff}")
        logger.debug(f"k_b T {temp}")
        base_prob = np.exp(-energy_diff/temp)
        logger.debug(f"base probability is {base_prob}")
        if np.random.rand() < base_prob:
            # succeeds! keep already changed slab
            # state = state.copy()
            logger.debug("state changed!")
            energy = curr_energy
            accept = True
        else:
            # failed, keep current state and revert slab back to original
            state, slab = add_to_slab(slab, state, adsorbate, coords, site1_idx) 
            state, slab = remove_from_slab(slab, state, site2_idx)

            logger.debug("state kept the same")
            energy = prev_energy
            accept = False
            
        # import pdb; pdb.set_trace()
    return state, slab, energy, accept


def spin_flip(state, slab, temp, pot, coords, connectivity, prev_energy=None, save_cif=False, iter=1, site_idx=None, testing=False, folder_name=".", adsorbate='Cu', relax=False):
    """Based on the Ising model, models the adsorption/desorption of atoms from surface lattice sites
    Uses semi-grand canonical ensemble, number of atoms allowed to vary.

    Parameters
    ----------
    state : np.array
        dimension the number of sites
    slab : catkit.gratoms.Gratoms
        model of the surface slab
    temp : float
        temperature
    pot : float
        chemical potential of metal

    Returns
    -------
    np.array, float
        new state, energy change
    """

    # choose a site to flip
    # coords, connectivity, sym_idx = get_adsorption_sites(slab, symmetry_reduced=False)

    # import pdb; pdb.set_trace()

    if not site_idx:
        site_idx = get_random_idx(connectivity)
    rand_site = coords[site_idx]

    logger.debug(f"\n we are at iter {iter}")
    logger.debug(f"idx is {site_idx} with connectivity {connectivity[site_idx]} at {rand_site}")

    # determine if site vacant or filled
    filled = (state > 0)[site_idx]
    logger.debug(f"before proposed state is")
    logger.debug(state)

    # change in number of adsorbates (atoms)
    delta_N = 0
                            
    if not prev_energy:
        if relax:
            prev_energy = slab_energy(slab, relax=True)
        else:
            # calculate energy of current state
            prev_energy = slab_energy(slab)

    # case site is vacant (spin down)
    if not filled:
        delta_N = 1 # add one atom

        # modularize
        logger.debug("site is not filled, attempting to adsorb")
        logger.debug(f"current slab has {len(slab)} atoms")

        # tag the atom to be adsorbed with its to-be index (last position on slab)    
        # adsorbate_idx = len(slab)
        # state[site_idx] = adsorbate_idx
        # slab.append(adsorbate)
        # slab.positions[-1] = coords[site_idx]

        state, slab = add_to_slab(slab, state, adsorbate, coords, site_idx)

        # import pdb; pdb.set_trace()
        logger.debug(f"proposed slab has {len(slab)} atoms")

    # case site is filled (spin up)
    else:
        delta_N = -1 # remove one Cu atom
        logger.debug("site is filled, attempting to desorb")
        logger.debug(f"current slab has {len(slab)} atoms")
        # adsorbate_idx = state[site_idx]
        # # slab_tags = slab.get_tags()

        # assert len(np.argwhere(state==adsorbate_idx)) <= 1, "more than 1 site found"
        # assert len(np.argwhere(state==adsorbate_idx)) == 1, "no sites found"
        # # np.argwhere(state==adsorbate_idx)[0,0]

        # # import pdb;pdb.set_trace()

        # # proposed_slab = slab.copy()
        # # import pdb; pdb.set_trace()
        # del slab[int(adsorbate_idx)] #networkxx needs python int 
        # # import pdb; pdb.set_trace()
        # # import pdb; pdb.set_trace()

        # # lower the index for higher index items
        # state = np.where(state>=int(adsorbate_idx), state-1, state)
        # # remove negatives
        # state = np.where(state<0, 0, state)

        # # remove the adsorbate from tracking
        # state[site_idx] = 0

        state, slab = remove_from_slab(slab, state, site_idx)

        logger.debug(f"proposed slab has {len(slab)} atoms")
    
    logger.debug(f"after proposed state is")
    logger.debug(state)

    # import pdb; pdb.set_trace()
    if save_cif:
        if not os.path.exists(folder_name):
            os.makedirs(folder_name)
        write(f'{folder_name}/proposed_slab_iter_{iter:03}.cif', slab)

    # to test, always accept
    accept = False
    if testing:
        energy = 0
    else:
        if relax:
            # use relaxation only to get lowest energy
            # but don't update adsorption positions
            curr_energy = slab_energy(slab, relax=True)
        else:
            curr_energy = slab_energy(slab)

        logger.debug(f"prev energy is {prev_energy}")
        logger.debug(f"curr energy is {curr_energy}")

        # energy change due to flipping spin
        energy_diff = curr_energy - prev_energy

        # check if transition succeeds
        # min(1, exp(-(\delta_E-(delta_N*pot))))
        logger.debug(f"energy diff is {energy_diff}")
        logger.debug(f"potential is {pot}")
        logger.debug(f"delta_N {delta_N}")
        logger.debug(f"k_b T {temp}")
        base_prob = np.exp(-(energy_diff-pot*delta_N)/temp)
        logger.debug(f"base probability is {base_prob}")
        if np.random.rand() < base_prob:
            # succeeds! keep already changed slab
            # state = state.copy()
            logger.debug("state changed!")
            energy = curr_energy
            accept = True
        else:
            # failed, keep current state and revert slab back to original
            if not filled:
                # removed filled
                # del slab[int(adsorbate_idx)]
                # # remove the adsorbate from tracking
                # state[site_idx] = 0
                state, slab = remove_from_slab(slab, state, site_idx)
            else:
                # add back removed
                # adsorbate_idx = len(slab)
                # state[site_idx] = adsorbate_idx
                # slab.append(adsorbate)
                # slab.positions[-1] = coords[site_idx]
                state, slab = add_to_slab(slab, state, adsorbate, coords, site_idx)

            logger.debug("state kept the same")
            energy = prev_energy
            accept = False
            
    return state, slab, energy, accept

def add_to_slab(slab, state, adsorbate, coords, site_idx):
    '''It adds an adsorbate to a slab, and updates the state to reflect the new adsorbate
    
    Parameters
    ----------
    slab : ase.Atoms
        the slab we're adding adsorbates to
    state : list
        a list of integers, where each integer represents the slab index of the adsorbate on that site. If the
    site is empty, the integer is 0.
    adsorbate : ase.Atoms
        the adsorbate molecule
    coords : list
        the coordinates of the sites on the surface
    site_idx : int
        the index of the site on the slab where the adsorbate will be placed
    
    Returns
    -------
        The state and slab are being returned.
    '''

    adsorbate_idx = len(slab)
    state[site_idx] = adsorbate_idx
    slab.append(adsorbate)
    slab.positions[-1] = coords[site_idx]
    return state, slab

def remove_from_slab(slab, state, site_idx):
    '''Remove the adsorbate from the slab and update the state
    
    Parameters
    ----------
    slab : ase.Atoms
        the slab object
    state : list
        a list of integers, where each integer represents the slab index of the adsorbate on that site. If the
    site is empty, the integer is 0.
    site_idx : int
        the index of the site to remove the adsorbate from
    
    Returns
    -------
        The state and slab are being returned.
    '''
    adsorbate_idx = state[site_idx]
    assert len(np.argwhere(state==adsorbate_idx)) <= 1, "more than 1 site found"
    assert len(np.argwhere(state==adsorbate_idx)) == 1, "no sites found"

    del slab[int(adsorbate_idx)]

    # lower the index for higher index items
    state = np.where(state>=int(adsorbate_idx), state-1, state)
    # remove negatives
    state = np.where(state<0, 0, state)

    # remove the adsorbate from tracking
    state[site_idx] = 0
    return state, slab

def get_adsorption_coords(slab, atom, connectivity):
    '''Takes a slab, an atom, and a list of site indices, and returns the actual coordinates of the
    adsorbed atoms
    
    Parameters
    ----------
    slab : ase.Atoms
        the original slab
    atom : ase.Atoms
        the atom you want to adsorb
    connectivity : list
        list of lists of integers, each list is a list of the indices of the atoms that are connected to
    the atom at the index of the list.
    
    Returns
    -------
        The positions of the adsorbed atoms.
    
    '''
    logger.debug(f"getting actual adsorption site coordinates")
    new_slab = slab.copy()

    proposed_slab_builder = catkit.gen.adsorption.Builder(new_slab)

    # add multiple adsorbates
    site_indices = list(range(len(connectivity)))

    # use proposed_slab_builder._single_adsorption multiple times
    for i, index in enumerate(site_indices):
        new_slab = proposed_slab_builder._single_adsorption(
                atom,
                bond=0,
                slab=new_slab,
                site_index=site_indices[i],
                auto_construct=False,
                symmetric=False)

    write(f'ads_{str(atom.symbols)}_all_adsorbed_slab.cif', new_slab)

    # store the actual positions of the sides
    logger.debug(f"new slab has {len(new_slab)} atoms and original slab has {len(slab)} atoms.")

    return new_slab.get_positions()[len(slab):]

def mcmc_run(num_sweeps=1000, temp=1, pot=1, alpha=0.9, slab=None, calc=EAM(potential=os.path.join(os.path.dirname(os.path.realpath(__file__)),'potentials','Cu2.eam.fs')), surface_name=None, element='Cu', canonical=False, num_ads_atoms=0, ads_coords=[], testing=False, adsorbate=None, relax=False):
    '''Performs MCMC sweep with given parameters, initializing with a random slab if not given an input.
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
    adsorbate : ase.Atoms
        the element to adsorb
    relax, optional
        whether to relax the slab after adsorption
    
    Returns
    -------
        history is a list of slab objects, energy_hist is a list of energies, frac_accept_hist is a list of
    fraction of accepted moves, adsorption_count_hist is a dictionary of lists of adsorption counts for
    each site type

    '''

    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO, datefmt='%m/%d/%Y %I:%M:%S %p')

    logger.info(f"Running with num_sweeps = {num_sweeps}, temp = {temp}, pot = {pot}, alpha = {alpha}")
    # Cu lattice at 293 K, 3.6147 Å, potential ranges from 0 - 2
    if type(slab) is not (catkit.gratoms.Gratoms or ase.Atoms):
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

    # get ALL the adsorption sites
    # top should have connectivity 1, bridge should be 2 and hollow more like 4
    coords, connectivity, sym_idx = get_adsorption_sites(slab, symmetry_reduced=False)

    # get absolute adsorption coords
    metal = catkit.gratoms.Gratoms(element)

    # set surface_name
    if not surface_name:
        surface_name = element
    
    # import pdb; pdb.set_trace()

    if not ((isinstance(ads_coords, list) and len(ads_coords) > 0) or isinstance(ads_coords, np.ndarray)):
        ads_coords = get_adsorption_coords(slab, metal, connectivity)
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

    run_folder = f"{surface_name}/runs{num_sweeps}_temp{temp}_pot{pot}_alpha{alpha}_{start_timestamp}"

    if canonical:
        # perform canonical runs
        # adsorb num_ads_atoms 
        assert num_ads_atoms > 0, "for canonical runs, need number of adsorbed atoms greater than 0"

        # following doesn't work because of infinite energies
        # for i in range(num_ads_atoms):
            # site_idx = get_random_idx(connectivity)
            # state, slab = add_to_slab(slab, state, element, ads_coords, site_idx)

        # perform grand canonical until num_ads_atoms are obtained
        while len(slab) < pristine_atoms + num_ads_atoms:
            state, slab, energy, accept = spin_flip(state, slab, temp, 0, ads_coords, connectivity, prev_energy=energy, save_cif=False, testing=testing, adsorbate=element, relax=relax)

        slab.write(f'{surface_name}_canonical_init.cif')

        # customize run folder
        run_folder = f"{surface_name}/runs{num_sweeps}_temp{temp}_adsatoms{num_ads_atoms:02}_alpha{alpha}_{start_timestamp}"

    # import pdb; pdb.set_trace()
    history = []
    energy_hist = np.random.rand(num_sweeps)
    # energy_sq_hist = np.random.rand(num_sweeps)
    adsorption_count_hist = defaultdict(list)
    frac_accept_hist = np.random.rand(num_sweeps)

    # sweep over # sites
    sweep_size = len(ads_coords)

    logger.info(f"running for {sweep_size} iterations per run over a total of {num_sweeps} runs")

    site_types = set(connectivity)

    # set adsorbate
    if not adsorbate:
        adsorbate = element
    logger.info(f"adsorbate is {adsorbate}")

    # import pdb; pdb.set_trace()

    for i in range(num_sweeps):
        num_accept = 0
        # simulated annealing schedule
        curr_temp = temp * alpha**i
        logger.info(f"In sweep {i+1} out of {num_sweeps}")
        for j in range(sweep_size):
            # logger.info(f"In iter {j+1}")
            run_idx = sweep_size*i + j+1
            if canonical:
                state, slab, energy, accept = spin_flip_canonical(state, slab, curr_temp, ads_coords, connectivity, prev_energy=energy, save_cif=False, iter=run_idx, testing=testing, folder_name=run_folder, adsorbate=adsorbate, relax=relax)
            else:
                state, slab, energy, accept = spin_flip(state, slab, curr_temp, pot, ads_coords, connectivity, prev_energy=energy, save_cif=False, iter=run_idx, testing=testing, folder_name=run_folder, adsorbate=adsorbate, relax=relax)
            num_accept += accept

        # end of sweep; append to history
        history.append(slab.copy())
        
        # save cif file
        if not os.path.exists(run_folder):
            os.makedirs(run_folder)
        write(f'{run_folder}/final_slab_run_{i+1:03}.cif', slab)

        if relax:
            opt_slab = optimize_slab(slab)
            opt_slab.write(f'{run_folder}/optim_slab_run_{i+1:03}.cif')

        logger.info(f"optim structure has Energy = {energy}")

        # append values
        energy_hist[i] = energy
        # energy_sq_hist[i] = energy**2
        # import pdb; pdb.set_trace()
        ads_counts = count_adsorption_sites(slab, state, connectivity)
        for key in set(site_types):
            if ads_counts[key]:
                adsorption_count_hist[key].append(ads_counts[key])
            else:
                adsorption_count_hist[key].append(0)

        frac_accept = num_accept/sweep_size
        frac_accept_hist[i] = frac_accept

    return history, energy_hist, frac_accept_hist, adsorption_count_hist


def count_adsorption_sites(slab, state, connectivity):
    '''It takes a slab, a state, and a connectivity matrix, and returns a dictionary of the number of
    adsorption sites of each type
    
    Parameters
    ----------
    slab
        ase.Atoms object
    state
        a list of the same length as the number of sites in the slab.
    connectivity
        a list of the same length as the number of sites in the slab.
    
    Returns
    -------
        A dictionary with the number of adsorption sites as keys and the number of atoms with that number
    of adsorption sites as values.
    
    '''
    occ_idx = state > 0
    return Counter(connectivity[occ_idx])


if __name__ == "__main__":
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG, datefmt='%m/%d/%Y %I:%M:%S %p')

    do_profiling = True

    # use EAM
    # eam_calc = EAM(potential='Cu2.eam.fs')

    # use LAMMPS
    alloy_parameters = {
        'pair_style': 'eam/alloy',
        'pair_coeff': ['* * cu_ag_ymwu.eam.alloy Ag']
    }
    alloy_potential_file = os.path.join(os.environ["PROJECT_DIR"], 'cu_ag_ymwu.eam.alloy')
    alloy_calc = LAMMPS(files=[alloy_potential_file], keep_tmp_files=False, keep_alive=False, tmp_dir="/home/dux/surface_sampling/tmp_files")
    alloy_calc.set(**alloy_parameters)

    # Au from standard cell
    atoms = read('Ag_mp-124_conventional_standard.cif')
    slab, surface_atoms = surfaces.surface_from_bulk(atoms, [1,1,1], size=[5,5])
    slab.write('Ag_111_5x5_pristine_slab.cif')

    element = 'Ag'
    # num_ads_atoms = 16 + 8
    adsorbate = 'Cu'
    if do_profiling:
        with cProfile.Profile() as pr:
            start = perf_counter()
            # chem pot 0 to less complicate things
            # temp in terms of kbT
            # history, energy_hist, frac_accept_hist, adsorption_count_hist = mcmc_run(num_sweeps=10, temp=1, pot=0, slab=slab, calc=lammps_calc, element=element, canonical=True, num_ads_atoms=num_ads_atoms)
            history, energy_hist, frac_accept_hist, adsorption_count_hist = mcmc_run(num_sweeps=1, temp=1, pot=0, alpha=0.99, slab=slab, calc=alloy_calc, element=element, adsorbate=adsorbate)
            stop = perf_counter()
            logger.info(f"Time taken = {stop - start} seconds")
        
        with open('profiling_stats.txt', 'w') as stream:
            stats = Stats(pr, stream=stream)
            stats.strip_dirs()
            stats.sort_stats('time')
            stats.dump_stats('.prof_stats')
            stats.print_stats()
