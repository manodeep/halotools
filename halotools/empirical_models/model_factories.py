# -*- coding: utf-8 -*-
"""
Module storing the various factories used to build galaxy-halo models. 
"""

__all__ = ['ModelFactory', 'SubhaloModelFactory', 'HodModelFactory']
__author__ = ['Andrew Hearin']

import numpy as np
from copy import copy
from functools import partial

from astropy.extern import six
from abc import ABCMeta, abstractmethod, abstractproperty

from . import model_helpers as model_helpers
from . import model_defaults
from . import mock_factories
from . import preloaded_hod_blueprints
from . import gal_prof_factory
from . import halo_prof_components

from ..sim_manager.read_nbody import ProcessedSnapshot
from ..sim_manager.generate_random_sim import FakeSim
from ..utils.array_utils import array_like_length as custom_len


@six.add_metaclass(ABCMeta)
class ModelFactory(object):
    """ Abstract container class used to build 
    any composite model of the galaxy-halo connection. 
    """

    def __init__(self, input_model_blueprint, **kwargs):
        """
        Parameters
        ----------
        input_model_blueprint : dict 
            Blueprint providing instructions for how to build the composite 
            model from a set of components. 

        galaxy_selection_func : function object, optional keyword argument 
            Function object that imposes a cut on the mock galaxies. 
            Function should take an Astropy table as a positional argument, 
            and return a boolean numpy array that will be 
            treated as a mask over the rows of the table. If not None, 
            the mask defined by ``galaxy_selection_func`` will be applied to the 
            ``galaxy_table`` after the table is generated by the `populate_mock` method. 
            Default is None.  
        """

        # Bind the model-building instructions to the composite model
        self._input_model_blueprint = input_model_blueprint

        if 'galaxy_selection_func' in kwargs.keys():
            self.galaxy_selection_func = kwargs['galaxy_selection_func']

    def populate_mock(self, **kwargs):
        """ Method used to populate a simulation using the model. 

        After calling this method, ``self`` will have a new ``mock`` attribute, 
        which has a ``galaxy_table`` bound to it containing the Monte Carlo 
        realization of the model. 

        Parameters 
        ----------
        snapshot : object, optional keyword argument
            Class instance of `~halotools.sim_manager.ProcessedSnapshot`. 
            This object contains the halo catalog and its metadata.  

        """

        if hasattr(self, 'mock'):
            self.mock.populate()
        else:
            if 'snapshot' in kwargs.keys():
                snapshot = kwargs['snapshot']
                # we need to delete the 'snapshot' keyword 
                # or else the call to mock_factories below 
                # will pass multiple snapshot arguments
                del kwargs['snapshot']
            else:
                snapshot = ProcessedSnapshot(**kwargs)

            mock_factory = self.model_blueprint['mock_factory']
            mock = mock_factory(snapshot=snapshot, model=self, **kwargs)
            self.mock = mock


class SubhaloModelFactory(ModelFactory):
    """ Class used to build any model of the galaxy-halo connection 
    in which there is a one-to-one correspondence between subhalos and galaxies.  

    Can be thought of as a factory that takes a model blueprint as input, 
    and generates a Subhalo Model object. The returned object can be used directly to 
    populate a simulation with a Monte Carlo realization of the model. 
    """

    def __init__(self, input_model_blueprint, **kwargs):
        """
        Parameters
        ----------
        input_model_blueprint : dict 
            The main dictionary keys of ``input_model_blueprint`` 
            are ``galprop_key`` strings, the names of 
            properties that will be assigned to galaxies 
            e.g., ``stellar_mass``, ``sfr``, ``morphology``, etc. 
            The dictionary value associated with each ``galprop_key``  
            is a class instance of the type of model that 
            maps that property onto subhalos. 

        galprop_sequence : list, optional
            Some model components may have explicit dependence upon 
            the value of some other galaxy model property. A classic 
            example is if the stellar mass of a central galaxy has explicit 
            dependence on whether or not the central is active or quiescent. 
            In such a case, you must pass a list of the galaxy properties 
            of the composite model; the first galprop in ``galprop_sequence`` 
            will be assigned first by the ``mock_factory``; the second galprop 
            in ``galprop_sequence`` will be assigned second, and its computation 
            may depend on the first galprop, and so forth. Default behavior is 
            to assume that no galprop has explicit dependence upon any other. 

        galaxy_selection_func : function object, optional keyword argument 
            Function object that imposes a cut on the mock galaxies. 
            Function should take an Astropy table as a positional argument, 
            and return a boolean numpy array that will be 
            treated as a mask over the rows of the table. If not None, 
            the mask defined by ``galaxy_selection_func`` will be applied to the 
            ``galaxy_table`` after the table is generated by the `populate_mock` method. 
            Default is None.  
        """

        super(SubhaloModelFactory, self).__init__(input_model_blueprint, **kwargs)

        self.model_blueprint = self._interpret_input_model_blueprint()
        
        self._build_composite_lists(**kwargs)

        self._set_init_param_dict()

        self._set_primary_behaviors()


    def _interpret_input_model_blueprint(self):

        model_blueprint = copy(self._input_model_blueprint)

        if 'mock_factory' not in model_blueprint.keys():
            model_blueprint['mock_factory'] = mock_factories.SubhaloMockFactory

        return model_blueprint

    def _set_primary_behaviors(self):
        """ Creates names and behaviors for the primary methods of `SubhaloModelFactory` 
        that will be used by the outside world.  

        Notes 
        -----
        The new methods created here are given standardized names, 
        for consistent communication with the rest of the package. 
        This consistency is particularly important for mock-making, 
        so that the `SubhaloModelFactory` can always call the same functions 
        regardless of the complexity of the model. 

        The behaviors of the methods created here are defined elsewhere; 
        `_set_primary_behaviors` just creates a symbolic link to those external behaviors. 
        """

        for galprop_key in self.galprop_list:
            
            behavior_name = 'mc_'+galprop_key
            behavior_function = partial(self._galprop_func, galprop_key)
            setattr(self, behavior_name, behavior_function)

    def _galprop_func(self, galprop_key, **kwargs):
        """
        """
        component_model = self.model_blueprint[galprop_key]
        current_galprop_param_dict = self._get_stripped_param_dict(galprop_key)
        behavior_function = partial(getattr(component_model, 'mc_'+galprop_key), 
            input_param_dict = current_galprop_param_dict)
        return behavior_function(**kwargs)

    def _build_composite_lists(self, **kwargs):
        """ A composite model has several bookkeeping devices that are built up from 
        the components: ``_haloprop_list``, ``publications``, and ``new_haloprop_func_dict``. 
        """

        unordered_galprop_list = [key for key in self.model_blueprint.keys() if key is not 'mock_factory']
        if 'galprop_sequence' in kwargs.keys():
            if set(kwargs['galprop_sequence']) != set(unordered_galprop_list):
                raise KeyError("The input galprop_sequence keyword argument must "
                    "have the same list of galprops as the input model blueprint")
            else:
                self.galprop_list = kwargs['galprop_sequence']
        else:
            self.galprop_list = unordered_galprop_list

        haloprop_list = []
        pub_list = []
        new_haloprop_func_dict = {}

        for galprop in self.galprop_list:
            component_model = self.model_blueprint[galprop]

            # haloprop keys
            if hasattr(component_model, 'prim_haloprop_key'):
                haloprop_list.append(component_model.prim_haloprop_key)
            if hasattr(component_model, 'sec_haloprop_key'):
                haloprop_list.append(component_model.sec_haloprop_key)

            # Reference list
            if hasattr(component_model, 'publications'):
                pub_list.extend(component_model.publications)

            # Haloprop function dictionaries
            if hasattr(component_model, 'new_haloprop_func_dict'):
                dict_intersection = set(new_haloprop_func_dict).intersection(
                    set(component_model.new_haloprop_func_dict))
                if dict_intersection == set():
                    new_haloprop_func_dict = (
                        new_haloprop_func_dict.items() + 
                        component_model.new_haloprop_func_dict.items()
                        )
                else:
                    example_repeated_element = list(dict_intersection)[0]
                    raise KeyError("The composite model received multiple "
                        "component models with a new_haloprop_func_dict that use "
                        "the %s key" % example_repeated_element)

        self._haloprop_list = list(set(haloprop_list))
        self.publications = list(set(pub_list))
        self.new_haloprop_func_dict = new_haloprop_func_dict

    def _set_init_param_dict(self):
        """ Method used to build a dictionary of parameters for the composite model. 

        Accomplished by retrieving all the parameters of the component models. 
        Method returns nothing, but binds ``param_dict`` to the class instance. 

        Notes 
        -----
        In MCMC applications, the items of ``param_dict`` define the 
        parameter set explored by the likelihood engine. 
        Changing the values of the parameters in ``param_dict`` 
        will propagate to the behavior of the component models. 

        Each component model has its own ``param_dict`` bound to it. 
        When changing the values of ``param_dict`` bound to `HodModelFactory`, 
        the corresponding values of the component model ``param_dict`` will *not* change.  

        """

        self.param_dict = {}

        # Loop over all galaxy types in the composite model
        for galprop in self.galprop_list:
            galprop_model = self.model_blueprint[galprop]

            if hasattr(galprop_model, 'param_dict'):
                galprop_model_param_dict = (
                    {galprop_model.galprop_key+'_'+key:val for key, val in galprop_model.param_dict.items()}
                    )
            else:
                galprop_model_param_dict = {}

            intersection = set(self.param_dict) & set(galprop_model_param_dict)
            if intersection != set():
                repeated_key = list(intersection)[0]
                raise KeyError("The param_dict key %s appears in more "
                    "than one component model" % repeated_key)
            else:

                self.param_dict = dict(
                    galprop_model_param_dict.items() + 
                    self.param_dict.items()
                    )

        self._init_param_dict = copy(self.param_dict)

    def _get_stripped_param_dict(self, galprop):
        """
        """

        galprop_model = self.model_blueprint[galprop]

        if hasattr(galprop_model, 'param_dict'):
            galprop_model_param_keys = galprop_model.param_dict.keys()
        else:
            galprop_model_param_keys = []

        output_param_dict = {}
        for key in galprop_model_param_keys:
            output_param_dict[key] = self.param_dict[galprop+'_'+key]

        return output_param_dict


    def restore_init_param_dict(self):
        """ Reset all values of the current ``param_dict`` to the values 
        the class was instantiated with. 

        Primary behaviors are reset as well, as this is how the 
        inherited behaviors get bound to the values in ``param_dict``. 
        """
        self.param_dict = self._init_param_dict
        self._set_primary_behaviors()


class HodModelFactory(ModelFactory):
    """ Class used to build HOD-style models of the galaxy-halo connection. 

    Can be thought of as a factory that takes an HOD model blueprint as input, 
    and generates an HOD Model object. The returned object can be used directly to 
    populate a simulation with a Monte Carlo realization of the model. 

    Most behavior is derived from external classes bound up in the input ``model_blueprint``. 
    So the purpose of `HodModelFactory` is mostly to compose these external 
    behaviors together into a composite model. 
    The aim is to provide a standardized model object 
    that interfaces consistently with the rest of the package, 
    regardless of the features of the model. 

    Notes 
    -----
    There are two main options for creating HOD-style blueprints 
    that can be passed to this class:

        * You can use one of the pre-computed blueprint found in `~halotools.empirical_models.preloaded_hod_blueprints` 
    
        * The following tutorial, :ref:`custom_hod_model_building_tutorial`, shows how you can build your own, customizing it based on the science you are interested in.  

    """

    def __init__(self, input_model_blueprint, **kwargs):
        """
        Parameters
        ----------
        input_model_blueprint : dict 
            The main dictionary keys of ``input_model_blueprint`` 
            are the names of the types of galaxies 
            found in the halos, 
            e.g., ``centrals``, ``satellites``, ``orphans``, etc. 
            The dictionary value associated with each ``gal_type`` key 
            is itself a dictionary whose keys 
            specify the type of model component, e.g., ``occupation``, 
            and values are class instances of that type of model. 
            The `interpret_input_model_blueprint` translates 
            ``input_model_blueprint`` into ``self.model_blueprint``.
            See :ref:`custom_hod_model_building_tutorial` for further details. 

        """

        super(HodModelFactory, self).__init__(input_model_blueprint, **kwargs)

        # Create attributes for galaxy types and their occupation bounds
        self._set_gal_types()
        self.model_blueprint = self.interpret_input_model_blueprint()

        # Build the composite model dictionary, 
        # whose keys are parameters of our model
        self._set_init_param_dict()

        # Build up and bind several lists from the component models
        self._build_composite_lists()

        # Create a set of bound methods with specific names 
        # that will be called by the mock factory 
        self._set_primary_behaviors()

    def interpret_input_model_blueprint(self):
        """ Method to interpret the ``input_model_blueprint`` 
        passed to the constructor into ``self.model_blueprint``: 
        the set of instructions that are actually used 
        by `HodModelFactory` to create the model. 

        Notes 
        ----- 
        In order for `HodModelFactory` to build a composite model object, 
        each galaxy's ``profile`` key of the ``model_blueprint`` 
        must be an instance of the 
        `~halotools.empirical_models.IsotropicGalProf` class. 
        However, if the user instead passed an instance of 
        `~halotools.empirical_models.HaloProfileModel`, there is no 
        ambiguity in what is desired: a profile model with parameters 
        that are unbiased with respect to the dark matter halo. 
        So the `interpret_input_model_blueprint` method translates 
        all such instances into `~halotools.empirical_models.IsotropicGalProf` instances, 
        and returns the appropriately modified blueprint, saving the user 
        a little rigamarole. 
        """

        model_blueprint = copy(self._input_model_blueprint)
        for gal_type in self.gal_types:
            input_prof_model = model_blueprint[gal_type]['profile']
#            if isinstance(input_prof_model, halo_prof_components.HaloProfileModel):
#                prof_model = gal_prof_factory.IsotropicGalProf(
#                    gal_type, input_prof_model)
#                model_blueprint[gal_type]['profile'] = prof_model

        if 'mock_factory' not in model_blueprint.keys():
            model_blueprint['mock_factory'] = mock_factories.HodMockFactory

        return model_blueprint 

    def _set_gal_types(self):
        """ Private method binding the ``gal_types`` list attribute,
        and the ``occupation_bound`` attribute, to the class instance. 

        The ``self.gal_types`` list is sequenced 
        in ascending order of the occupation bound. 
        """

        gal_types = [key for key in self._input_model_blueprint.keys() if key is not 'mock_factory']

        occupation_bounds = []
        for gal_type in gal_types:
            model = self._input_model_blueprint[gal_type]['occupation']
            occupation_bounds.append(model.occupation_bound)

        # Lists have been created. Now sort them and then bind the sorted lists to the instance. 
        sorted_idx = np.argsort(occupation_bounds)
        gal_types = list(np.array(gal_types)[sorted_idx])
        self.gal_types = gal_types

        self.occupation_bound = {}
        for gal_type in self.gal_types:
            self.occupation_bound[gal_type] = (
                self._input_model_blueprint[gal_type]['occupation'].occupation_bound)

    def _set_primary_behaviors(self):
        """ Creates names and behaviors for the primary methods of `HodModelFactory` 
        that will be used by the outside world.  

        Notes 
        -----
        The new methods created here are given standardized names, 
        for consistent communication with the rest of the package. 
        This consistency is particularly important for mock-making, 
        so that the `HodMockFactory` can always call the same functions 
        regardless of the complexity of the model. 

        The behaviors of the methods created here are defined elsewhere; 
        `_set_primary_behaviors` just creates a symbolic link to those external behaviors. 
        """

        for gal_type in self.gal_types:

            # Set the method used to return Monte Carlo realizations 
            # of per-halo gal_type abundance
            new_method_name = 'mc_occupation_'+gal_type
            occupation_model = self.model_blueprint[gal_type]['occupation']
            new_method_behavior = partial(occupation_model.mc_occupation, 
                input_param_dict = self.param_dict)
            setattr(self, new_method_name, new_method_behavior)

            # For convenience, also inherit  
            # the first moment of the occupation distribution 
            if hasattr(occupation_model, 'mean_occupation'):
                new_method_name = 'mean_occupation_'+gal_type
                new_method_behavior = partial(occupation_model.mean_occupation, 
                    input_param_dict = self.param_dict)
                setattr(self, new_method_name, new_method_behavior)

            # Create a new method to compute each (unbiased) halo profile parameter
            # For composite models in which multiple galaxy types have the same 
            # underlying dark matter profile, use the halo profile model of the 
            # first gal_type in the self.gal_types list 
            gal_prof_model = self.model_blueprint[gal_type]['profile']
            for prof_param_key in gal_prof_model.prof_param_keys:

                new_method_name = prof_param_key + '_halos'
                if not hasattr(self, new_method_name):
                    new_method_behavior = getattr(gal_prof_model.halo_prof_model, prof_param_key)
                    setattr(self, new_method_name, new_method_behavior)

                new_method_name = prof_param_key + '_' + gal_type
                new_method_behavior = getattr(gal_prof_model, prof_param_key)
                setattr(self, new_method_name, new_method_behavior)

            ### Create a method to assign Monte Carlo-realized 
            # positions to each gal_type
            new_method_name = 'pos_'+gal_type
            new_method_behavior = partial(self.mc_pos, gal_type = gal_type)
            setattr(self, new_method_name, new_method_behavior)

        for prof_param_key in self.prof_param_keys:
            for gal_type in self.gal_types:
                gal_prof_param_method_name = prof_param_key+'_'+gal_type
                if not hasattr(self, gal_prof_param_method_name):
                    halo_prof_param_method_name = prof_param_key+'_halos'
                    halo_prof_param_method_behavior = getattr(self, halo_prof_param_method_name)
                    setattr(self, gal_prof_param_method_name, halo_prof_param_method_behavior)

    def mc_pos(self, **kwargs):
        """ Method used to generate Monte Carlo realizations of galaxy positions. 

        Identical to component model version from which the behavior derives, 
        only this method re-scales the halo-centric distance by the halo radius, 
        and re-centers the re-scaled output of the component model to the halo position.

        Parameters 
        ----------
        galaxy_table : Astropy Table, required keyword argument
            Data table storing a length-Ngals galaxy catalog. 

        gal_type : string, required keyword argument
            Name of the galaxy population. 

        Returns 
        -------
        x, y, z : array_like 
            Length-Ngals arrays of coordinate positions.

        Notes 
        -----
        This method is not directly called by 
        `~halotools.empirical_models.mock_factories.HodMockFactory`. 
        Instead, the `_set_primary_behaviors` method calls functools.partial 
        to create a ``mc_pos_gal_type`` method for each ``gal_type`` in the model. 

        """
        galaxy_table = kwargs['galaxy_table']
        gal_type = kwargs['gal_type']
        gal_prof_model = self.model_blueprint[gal_type]['profile']
        x, y, z = gal_prof_model.mc_pos(galaxy_table=galaxy_table)

        # Re-scale the halo-centric distance by the halo boundary
        halo_boundary_key = model_defaults.host_haloprop_prefix + gal_prof_model.halo_boundary
        x *= galaxy_table[halo_boundary_key]/1000.
        y *= galaxy_table[halo_boundary_key]/1000.
        z *= galaxy_table[halo_boundary_key]/1000.

        # Re-center the positions by the host halo location
        halo_xpos_key = model_defaults.host_haloprop_prefix+'x'
        halo_ypos_key = model_defaults.host_haloprop_prefix+'y'
        halo_zpos_key = model_defaults.host_haloprop_prefix+'z'
        x += galaxy_table[halo_xpos_key]
        y += galaxy_table[halo_ypos_key]
        z += galaxy_table[halo_zpos_key]

        return x, y, z

    def build_halo_prof_lookup_tables(self, **kwargs):
        """ Method to create a lookup table 
        used to generate Monte Carlo realizations of 
        radial profiles of galaxies. 

        """

        for gal_type in self.gal_types:
            halo_prof_model = self.model_blueprint[gal_type]['profile'].halo_prof_model
            halo_prof_model.build_inv_cumu_lookup_table(**kwargs)

    def _set_init_param_dict(self):
        """ Method used to build a dictionary of parameters for the composite model. 

        Accomplished by retrieving all the parameters of the component models. 
        Method returns nothing, but binds ``param_dict`` to the class instance. 

        Notes 
        -----
        In MCMC applications, the items of ``param_dict`` define the 
        parameter set explored by the likelihood engine. 
        Changing the values of the parameters in ``param_dict`` 
        will propagate to the behavior of the component models. 

        Each component model has its own ``param_dict`` bound to it. 
        When changing the values of ``param_dict`` bound to `HodModelFactory`, 
        the corresponding values of the component model ``param_dict`` will *not* change.  

        """

        self.param_dict = {}

        # Loop over all galaxy types in the composite model
        for gal_type in self.gal_types:
            gal_type_dict = self.model_blueprint[gal_type]
            # For each galaxy type, loop over its features
            for model_instance in gal_type_dict.values():

                intersection = set(self.param_dict) & set(model_instance.param_dict)
                if intersection != set():
                    repeated_key = list(intersection)[0]
                    raise KeyError("The param_dict key %s appears in more "
                        "than one component model" % repeated_key)
                else:

                    self.param_dict = dict(
                        model_instance.param_dict.items() + 
                        self.param_dict.items()
                        )

        self._init_param_dict = copy(self.param_dict)

    def restore_init_param_dict(self):
        """ Reset all values of the current ``param_dict`` to the values 
        the class was instantiated with. 

        Primary behaviors are reset as well, as this is how the 
        inherited behaviors get bound to the values in ``param_dict``. 
        """
        self.param_dict = self._init_param_dict
        self._set_primary_behaviors()

    def _build_composite_lists(self):
        """ A composite model has several lists that are built up from 
        the components: ``_haloprop_list``, ``publications``, and 
        ``new_haloprop_func_dict``. 
        """

        haloprop_list = []
        prof_param_keys = []
        pub_list = []
        new_haloprop_func_dict = {}

        for gal_type in self.gal_types:
            component_dict = self.model_blueprint[gal_type]
            for component_key in component_dict.keys():
                component_model = component_dict[component_key]

                # haloprop keys
                if hasattr(component_model, 'prim_haloprop_key'):
                    haloprop_list.append(component_model.prim_haloprop_key)
                if hasattr(component_model, 'sec_haloprop_key'):
                    haloprop_list.append(component_model.sec_haloprop_key)

                # halo profile parameter keys
                if hasattr(component_model, 'prof_param_keys'):
                    prof_param_keys.extend(component_model.prof_param_keys)

                # Reference list
                if hasattr(component_model, 'publications'):
                    pub_list.extend(component_model.publications)

                # Haloprop function dictionaries
                if hasattr(component_model, 'new_haloprop_func_dict'):
                    dict_intersection = set(new_haloprop_func_dict).intersection(
                        set(component_model.new_haloprop_func_dict))
                    if dict_intersection == set():
                        new_haloprop_func_dict = (
                            new_haloprop_func_dict.items() + 
                            component_model.new_haloprop_func_dict.items()
                            )
                    else:
                        example_repeated_element = list(dict_intersection)[0]
                        raise KeyError("The composite model received multiple "
                            "component models with a new_haloprop_func_dict that use "
                            "the %s key" % example_repeated_element)

        self._haloprop_list = list(set(haloprop_list))
        self.prof_param_keys = list(set(prof_param_keys))
        self.publications = list(set(pub_list))
        self.new_haloprop_func_dict = new_haloprop_func_dict



##########################################











