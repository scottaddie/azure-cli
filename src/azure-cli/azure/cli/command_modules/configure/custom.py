# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import json
import os

import configparser
from knack.log import get_logger
from knack.prompting import prompt, prompt_y_n, prompt_choice_list, NoTTYException
from knack.util import CLIError

from azure.cli.core.util import ConfiguredDefaultSetter

from azure.cli.command_modules.configure._consts import (OUTPUT_LIST,
                                                         MSG_INTRO,
                                                         MSG_CLOSING,
                                                         MSG_GLOBAL_SETTINGS_LOCATION,
                                                         MSG_HEADING_CURRENT_CONFIG_INFO,
                                                         MSG_HEADING_ENV_VARS,
                                                         MSG_PROMPT_MANAGE_GLOBAL,
                                                         MSG_PROMPT_GLOBAL_OUTPUT,
                                                         MSG_PROMPT_TELEMETRY,
                                                         MSG_PROMPT_FILE_LOGGING,
                                                         MSG_PROMPT_CACHE_TTL,
                                                         WARNING_CLOUD_FORBID_TELEMETRY,
                                                         DEFAULT_CACHE_TTL)
from azure.cli.command_modules.configure._utils import get_default_from_config

answers = {}

logger = get_logger(__name__)


def _print_cur_configuration(file_config):
    from azure.cli.core._config import ENV_VAR_PREFIX
    print(MSG_HEADING_CURRENT_CONFIG_INFO)
    for section in file_config.sections():
        print()
        print('[{}]'.format(section))
        for option in file_config.options(section):
            print('{} = {}'.format(option, file_config.get(section, option)))
    env_vars = [ev for ev in os.environ if ev.startswith(ENV_VAR_PREFIX)]
    if env_vars:
        print(MSG_HEADING_ENV_VARS)
        print('\n'.join(['{} = {}'.format(ev, os.environ[ev]) for ev in env_vars]))


def _handle_global_configuration(config, cloud_forbid_telemetry):
    # print location of global configuration
    print(MSG_GLOBAL_SETTINGS_LOCATION.format(config.config_path))
    # set up the config parsers
    file_config = configparser.ConfigParser()
    config_exists = file_config.read([config.config_path])
    should_modify_global_config = False
    if config_exists:
        # print current config and prompt to allow global config modification
        _print_cur_configuration(file_config)
        should_modify_global_config = prompt_y_n(MSG_PROMPT_MANAGE_GLOBAL, default='n')
        answers['modify_global_prompt'] = should_modify_global_config
    if not config_exists or should_modify_global_config:
        # no config exists yet so configure global config or user wants to modify global config
        with ConfiguredDefaultSetter(config, False):
            output_index = prompt_choice_list(MSG_PROMPT_GLOBAL_OUTPUT, OUTPUT_LIST,
                                              default=get_default_from_config(config,
                                                                              'core', 'output',
                                                                              OUTPUT_LIST))
            answers['output_type_prompt'] = output_index
            answers['output_type_options'] = str(OUTPUT_LIST)
            enable_file_logging = prompt_y_n(MSG_PROMPT_FILE_LOGGING, default='n')
            if cloud_forbid_telemetry:
                allow_telemetry = False
            else:
                allow_telemetry = prompt_y_n(MSG_PROMPT_TELEMETRY, default='y')
            answers['telemetry_prompt'] = allow_telemetry
            cache_ttl = None
            while not cache_ttl:
                try:
                    cache_ttl = prompt(MSG_PROMPT_CACHE_TTL) or DEFAULT_CACHE_TTL
                    # ensure valid int by casting
                    cache_value = int(cache_ttl)
                    if cache_value < 1:
                        raise ValueError
                except ValueError:
                    logger.error('TTL must be a positive integer')
                    cache_ttl = None
            # save the global config
            config.set_value('core', 'output', OUTPUT_LIST[output_index]['name'])
            config.set_value('core', 'collect_telemetry', 'yes' if allow_telemetry else 'no')
            config.set_value('core', 'cache_ttl', cache_ttl)
            config.set_value('logging', 'enable_log_file', 'yes' if enable_file_logging else 'no')


# pylint: disable=inconsistent-return-statements
def handle_configure(cmd, defaults=None, list_defaults=None, scope=None):
    from azure.cli.core.cloud import cloud_forbid_telemetry, get_active_cloud_name
    if defaults:
        defaults_section = cmd.cli_ctx.config.defaults_section_name
        with ConfiguredDefaultSetter(cmd.cli_ctx.config, scope.lower() == 'local'):
            for default in defaults:
                parts = default.split('=', 1)
                if len(parts) == 1:
                    raise CLIError('usage error: --defaults STRING=STRING STRING=STRING ...')
                cmd.cli_ctx.config.set_value(defaults_section, parts[0], _normalize_config_value(parts[1]))
        return
    if list_defaults:
        with ConfiguredDefaultSetter(cmd.cli_ctx.config, scope.lower() == 'local'):
            defaults_result = cmd.cli_ctx.config.items(cmd.cli_ctx.config.defaults_section_name)
        return [x for x in defaults_result if x.get('value')]

    # if nothing supplied, we go interactively
    try:
        print(MSG_INTRO)
        cloud_forbid_telemetry = cloud_forbid_telemetry(cmd.cli_ctx)
        _handle_global_configuration(cmd.cli_ctx.config, cloud_forbid_telemetry)
        print(MSG_CLOSING)
        if cloud_forbid_telemetry:
            logger.warning(WARNING_CLOUD_FORBID_TELEMETRY, get_active_cloud_name(cmd.cli_ctx))
        # TODO: log_telemetry('configure', **answers)
    except NoTTYException:
        raise CLIError('This command is interactive and no tty available.')
    except (EOFError, KeyboardInterrupt):
        print()


def _normalize_config_value(value):
    if value:
        value = '' if value in ["''", '""'] else value
    return value


def _get_cache_directory(cli_ctx):
    from azure.cli.core.commands.client_factory import get_subscription_id
    from azure.cli.core._environment import get_config_dir
    return os.path.join(
        get_config_dir(),
        'object_cache',
        cli_ctx.cloud.name,
        get_subscription_id(cli_ctx))


def list_cache_contents(cmd):
    from glob import glob
    directory = _get_cache_directory(cmd.cli_ctx)
    contents = []
    rg_paths = glob(os.path.join(directory, '*'))
    for rg_path in rg_paths:
        rg_name = os.path.split(rg_path)[1]
        for dir_name, _, file_list in os.walk(rg_path):
            if not file_list:
                continue
            resource_type = os.path.split(dir_name)[1]
            for f in file_list:
                file_path = os.path.join(dir_name, f)
                try:
                    with open(file_path, 'r') as cache_file:
                        cache_obj = json.loads(cache_file.read())
                        contents.append({
                            'resourceGroup': rg_name,
                            'resourceType': resource_type,
                            'name': f.split('.', 1)[0],
                            'lastSaved': cache_obj['last_saved']
                        })
                except KeyError:
                    # invalid cache entry
                    logger.debug('Removing corrupt cache file: %s', file_path)
                    os.remove(file_path)
    return contents


def show_cache_contents(cmd, resource_group_name, item_name, resource_type):
    directory = _get_cache_directory(cmd.cli_ctx)
    item_path = os.path.join(directory, resource_group_name, resource_type, '{}.json'.format(item_name))
    try:
        with open(item_path, 'r') as cache_file:
            cache_obj = json.loads(cache_file.read())
    except OSError:
        raise CLIError('Not found in cache: {}'.format(item_path))
    return cache_obj['_payload']


def delete_cache_contents(cmd, resource_group_name, item_name, resource_type):
    directory = _get_cache_directory(cmd.cli_ctx)
    item_path = os.path.join(directory, resource_group_name, resource_type, '{}.json'.format(item_name))
    try:
        os.remove(item_path)
    except OSError:
        logger.info('%s not found in object cache.', item_path)


def purge_cache_contents():
    import shutil
    from azure.cli.core._environment import get_config_dir
    directory = os.path.join(get_config_dir(), 'object_cache')
    try:
        shutil.rmtree(directory)
    except OSError as ex:
        logger.debug(ex)
