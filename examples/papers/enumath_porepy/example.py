import numpy as np
import scipy.sparse as sps
import logging

from porepy.viz import exporter
from porepy.fracs import importer

from porepy.params import tensor
from porepy.params.bc import BoundaryCondition
from porepy.params.data import Parameters

from porepy.grids.grid import FaceTag
from porepy.grids import coarsening

from porepy.numerics import elliptic

#------------------------------------------------------------------------------#

def add_data(gb, domain, tol):
    gb.add_node_props(['param', 'if_tangent', 'frac_num'])

    apert = 1e-2

    km = 1
    kf_low = 1e-4
    kf_high = 1e4
    special_fracture = 6

    for g, d in gb:

        param = Parameters(g)
        d['if_tangent'] = True
        if g.dim == 3:
            kxx = km
            d['frac_num'] = -1*np.ones(g.num_cells)
        elif g.dim == 2:
            d['frac_num'] = g.frac_num*np.ones(g.num_cells)
            if g.frac_num == special_fracture:
                kxx = kf_high
            else:
                kxx = kf_low
        else: # g.dim == 1
            neigh = gb.node_neighbors(g, only_higher=True)
            d['frac_num'] = -1*np.ones(g.num_cells)
            frac_num = np.array([gh.frac_num for gh in neigh])
            if np.any(frac_num == special_fracture):
                kxx = kf_high
            else:
                kxx = kf_low

        perm = tensor.SecondOrder(g.dim, kxx*np.ones(g.num_cells))
        param.set_tensor("flow", perm)

        param.set_source("flow", np.zeros(g.num_cells))

        param.set_aperture(np.power(apert, gb.dim_max() - g.dim))

        bound_faces = g.get_domain_boundary_faces()
        if bound_faces.size != 0:
            bound_face_centers = g.face_centers[:, bound_faces]

            top = bound_face_centers[2, :] > domain['zmax'] - tol
            bottom = bound_face_centers[2, :] < domain['zmin'] + tol

            boundary = np.logical_or(top, bottom)

            labels = np.array(['neu'] * bound_faces.size)
            labels[boundary] = ['dir']

            bc_val = np.zeros(g.num_faces)
            bc_val[bound_faces[bottom]] = 1

            param.set_bc("flow", BoundaryCondition(g, bound_faces, labels))
            param.set_bc_val("flow", bc_val)
        else:
            param.set_bc("flow", BoundaryCondition(
                g, np.empty(0), np.empty(0)))

        d['param'] = param

    # Assign coupling permeability
    gb.add_edge_prop('kn')
    for e, d in gb.edges_props():
        g_l, g_h = gb.sorted_nodes_of_edge(e)
        if g_h.dim == gb.dim_max() and g_l.frac_num == special_fracture:
            kxx = kf_high
        elif g_h.dim < gb.dim_max() and g_h.frac_num == special_fracture:
            kxx = kf_high
        else:
            kxx = kf_low
        d['kn'] = kxx / gb.node_prop(g_l, 'param').get_aperture()

#------------------------------------------------------------------------------#

tol = 1e-6
coarse = True

problem_kwargs = {}
problem_kwargs['file_name'] = 'solution'
if coarse:
    problem_kwargs['folder_name'] = 'vem_coarse'
else:
    problem_kwargs['folder_name'] = 'vem'

h = 0.08
#vem vem_coarse
grid_kwargs = {}
grid_kwargs['mesh_size'] = {'mode': 'constant', 'value': h, 'bound_value': h,
                            'tol': tol}

file_dfm = 'dfm.csv'
gb, domain = importer.dfm_from_csv(file_dfm, tol, **grid_kwargs)
gb.compute_geometry()
if coarse:
    coarsening.coarsen(gb, 'by_volume')

internal_flag = FaceTag.FRACTURE
[g.remove_face_tag_if_tag(FaceTag.BOUNDARY, internal_flag) for g, _ in gb]

problem = elliptic.DualEllipticModel(gb, **problem_kwargs)

# Assign parameters
add_data(gb, domain, tol)

problem.solve()
problem.split()

problem.pressure('pressure')
problem.project_discharge('P0u')
problem.save(['pressure', 'P0u', 'frac_num'])

#------------------------------------------------------------------------------#
