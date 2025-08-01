import os
import logging
import json

from pathlib import Path
from dataclasses import dataclass, field, fields
from typing import Union, Dict, Any, Optional, List

from dacite import from_dict

import core.path_manager as Paths
import core.event_manager as Events

from core.utils.security import Security
from core import package_manager
from core.packages import launcher_package
from core.packages.model_importers import gimi_package
from core.packages.model_importers import srmi_package
from core.packages.model_importers import wwmi_package
from core.packages.model_importers import zzmi_package
from core.packages.model_importers import himi_package

log = logging.getLogger(__name__)


@dataclass
class ImportersConfig:
    GIMI: gimi_package.GIMIPackageConfig = field(default_factory=lambda: gimi_package.GIMIPackageConfig())
    SRMI: srmi_package.SRMIPackageConfig = field(default_factory=lambda: srmi_package.SRMIPackageConfig())
    WWMI: wwmi_package.WWMIPackageConfig = field(default_factory=lambda: wwmi_package.WWMIPackageConfig())
    ZZMI: zzmi_package.ZZMIPackageConfig = field(default_factory=lambda: zzmi_package.ZZMIPackageConfig())
    HIMI: himi_package.HIMIPackageConfig = field(default_factory=lambda: himi_package.HIMIPackageConfig())


@dataclass
class SecurityConfig:
    user_signature: str = ''


@dataclass
class AppConfig:
    # Config fields
    Launcher: launcher_package.LauncherManagerConfig = field(
        default_factory=lambda: launcher_package.LauncherManagerConfig()
    )
    Packages: package_manager.PackageManagerConfig = field(
        default_factory=lambda: package_manager.PackageManagerConfig()
    )
    Importers: ImportersConfig = field(
        default_factory=lambda: ImportersConfig()
    )
    Security: SecurityConfig = field(
        default_factory=lambda: SecurityConfig()
    )
    # State fields
    # Active: Optional[WWMIConfig] = field(init=False, default=None)

    active_theme: Optional[str] = field(init=False, default=None)

    def __post_init__(self):
        self.active_theme = 'Default'

    @property
    def theme_path(self) -> Path:
        return Paths.App.Themes / Config.active_theme

    @property
    def config_path(self):
        return Paths.App.Root / 'XXMI Launcher Config.json'

    @property
    def Active(self) -> Union[gimi_package.GIMIPackageConfig, srmi_package.SRMIPackageConfig,
                              zzmi_package.ZZMIPackageConfig, wwmi_package.WWMIPackageConfig,
                              himi_package.HIMIPackageConfig]:
        global Active
        return Active

    def as_dict(self, obj: Any) -> Dict[str, Any]:
        result = {}

        if hasattr(obj, '__dataclass_fields__'):
            # Process dataclass object
            for obj_field in fields(obj):
                # Fields with 'init=False' contain app state data that isn't supposed to be saved
                if not obj_field.init:
                    continue
                # Recursively process nested dataclass
                value = getattr(obj, obj_field.name)

                if hasattr(value, '__dataclass_fields__') or isinstance(value, dict | list | tuple):
                    result[obj_field.name] = self.as_dict(value)
                else:
                    result[obj_field.name] = value

        elif isinstance(obj, dict):
            # Process dict object
            for obj_field, value in obj.items():
                if hasattr(value, '__dataclass_fields__') or isinstance(value, dict | list | tuple):
                    result[obj_field] = self.as_dict(value)
                else:
                    result[obj_field] = value

        elif isinstance(obj, list | tuple):
            # Process list or tuple object
            result = []
            for value in obj:
                if hasattr(value, '__dataclass_fields__') or isinstance(value, dict | list | tuple):
                    result.append(self.as_dict(value))
                else:
                    result.append(value)

        return result

    def as_json(self):
        cfg = self.as_dict(self)
        return json.dumps(cfg, indent=4)

    def from_json(self, config_path: Path):
        cfg = self.as_dict(self)
        if config_path.is_file():
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg.update(json.load(f))
        for key, value in from_dict(data_class=AppConfig, data=cfg).__dict__.items():
            if hasattr(self, key):
                setattr(self, key, value)
        if self.Launcher.gui_theme:
            self.active_theme = self.Launcher.gui_theme

    def load(self, cfg_path=None):
        try:
            Config.from_json(cfg_path or self.config_path)
        except Exception as e:
            log.exception(e)
            raise e
        finally:
            global Launcher
            Launcher = self.Launcher
            global Packages
            Packages = self.Packages
            global Importers
            Importers = self.Importers

    def save(self):
        cfg = Config.as_json()
        with open(self.config_path, 'w', encoding='utf-8') as f:
            return f.write(cfg)

    def run_patch_184(self):
        for package_name, importer in self.Importers.__dict__.items():
            # Detect existing System > dll_initialization_delay
            dll_initialization_delay = 0
            try:
                from core.utils.ini_handler import IniHandler, IniHandlerSettings
                ini_path = importer.Importer.importer_path / 'd3dx.ini'
                Events.Fire(Events.Application.VerifyFileAccess(path=ini_path, write=True))
                with open(ini_path, 'r', encoding='utf-8') as f:
                    ini = IniHandler(IniHandlerSettings(ignore_comments=True), f)
                    dll_initialization_delay = ini.get_section('System').get_option('dll_initialization_delay')
                    if dll_initialization_delay is not None:
                        dll_initialization_delay = int(dll_initialization_delay)
                        log.debug(f'Detected existing dll_initialization_delay in for {package_name}: {dll_initialization_delay}')
                    else:
                        dll_initialization_delay = 0
            except Exception as e:
                log.debug(f'Failed to detect existing dll_initialization_delay in for {package_name}: {e}')

            # Reset Unsafe Mode for WWMI
            if package_name == 'WWMI':
                importer.Migoto.unsafe_mode = False
                importer.Migoto.unsafe_mode_signature = ''
                if dll_initialization_delay == 0:
                    dll_initialization_delay = 500

            # Keep existing dll_initialization_delay
            importer.Importer.xxmi_dll_init_delay = dll_initialization_delay

            log.debug(f'Set xxmi_dll_init_delay for {package_name} to {dll_initialization_delay}')

    def run_patch_186(self):
        importer = self.Importers.__dict__['WWMI']
        try:
            del importer.Importer.perf_tweaks['SystemSettings']['r.Streaming.LimitPoolSizeToVRAM']
        except:
            pass

    def upgrade(self, old_version, new_version):
        # Save config to file and exit early if old version is empty (aka fresh installation)
        if not old_version:
            log.debug(f'Saving new config...')
            self.Launcher.config_version = new_version
            self.save()
            return

        # Apply patches
        patches = {
            '1.8.4': self.run_patch_184,
            '1.8.6': self.run_patch_186,
        }
        applied_patches = []
        for patch_version, patch_func in patches.items():
            if old_version < patch_version:
                log.debug(f'Upgrading launcher config from {old_version} to {patch_version}...')
                patch_func()
                applied_patches.append(patch_version)

        # Save patched config to file
        if len(applied_patches) > 0:
            log.debug(f'Saving patched config...')
            self.Launcher.config_version = new_version
            self.save()


class AppConfigSecurity:
    def __init__(self):
        self.security = None

    def load(self, save_config: bool = True):
        global Config

        self.security = Security()

        keys_path = Paths.App.Resources / 'Security'
        Paths.verify_path(keys_path)
        try:
            self.security.read_key_pair(Paths.App.Resources / keys_path)
        except Exception as e:
            pass

        if self.security.public_key is None or not self.security.verify(Config.Security.user_signature,
                                                                        os.getlogin().encode()):
            self.security.generate_key_pair()
            self.security.write_key_pair(keys_path)
            Config.Security.user_signature = self.security.sign(os.getlogin())
            if save_config:
                Config.save()

    def validate_config(self):
        global Config

        unsecure_settings = [
            Config.Active.Migoto.unsafe_mode,
            Config.Active.Importer.run_pre_launch,
            Config.Active.Importer.custom_launch,
            Config.Active.Importer.run_post_load,
            Config.Active.Importer.extra_libraries,
        ]

        if not any(unsecure_settings):
            return

        if self.security is None:
            self.load()

        wrong_signatures = {}

        if Config.Active.Migoto.unsafe_mode:
            if not self.security.verify(Config.Active.Migoto.unsafe_mode_signature, os.getlogin().encode()):
                wrong_signatures['Unsafe Mode'] = 'Enabled'

        if Config.Active.Importer.run_pre_launch:
            if not self.security.verify(Config.Active.Importer.run_pre_launch_signature, Config.Active.Importer.run_pre_launch.encode()):
                wrong_signatures['Run Pre Launch'] = Config.Active.Importer.run_pre_launch

        if Config.Active.Importer.custom_launch:
            if not self.security.verify(Config.Active.Importer.custom_launch_signature, Config.Active.Importer.custom_launch.encode()):
                wrong_signatures['Custom Launch'] = Config.Active.Importer.custom_launch

        if Config.Active.Importer.run_post_load:
            if not self.security.verify(Config.Active.Importer.run_post_load_signature, Config.Active.Importer.run_post_load.encode()):
                wrong_signatures['Run Post Load'] = Config.Active.Importer.run_post_load

        if Config.Active.Importer.extra_libraries:
            if not self.security.verify(Config.Active.Importer.extra_libraries_signature, Config.Active.Importer.extra_libraries.encode()):
                wrong_signatures['Extra Libraries'] = Config.Active.Importer.extra_libraries

        if len(wrong_signatures) > 0:
            msg = '\n'.join([f'{k}: "{v}"' for k, v in wrong_signatures.items()])
            user_requested_reset = Events.Call(Events.Application.ShowError(
                modal=True,
                lock_master=False,
                screen_center=True,
                confirm_text='Reset',
                cancel_text='Keep',
                message=f'Failed to validate unsecure settings!\n\n'
                        f'{msg}\n'
            ))
            if user_requested_reset:
                if 'Unsafe Mode' in wrong_signatures:
                    Config.Active.Migoto.unsafe_mode = False
                if 'Run Pre Launch' in wrong_signatures:
                    Config.Active.Importer.run_pre_launch = ''
                if 'Custom Launch' in wrong_signatures:
                    Config.Active.Importer.custom_launch = ''
                if 'Run Post Load' in wrong_signatures:
                    Config.Active.Importer.run_post_load = ''
                if 'Extra Libraries' in wrong_signatures:
                    Config.Active.Importer.extra_libraries = ''
            else:
                self.sign_settings()

    def sign_settings(self, save_config: bool = True):
        global Active
        global Config
        if self.security is None:
            self.load(save_config=False)
        if Active.Migoto.unsafe_mode:
            Active.Migoto.unsafe_mode_signature = self.security.sign(os.getlogin().encode())
        if Active.Importer.run_pre_launch:
            Active.Importer.run_pre_launch_signature = self.security.sign(Active.Importer.run_pre_launch.encode())
        if Active.Importer.custom_launch:
            Active.Importer.custom_launch_signature = self.security.sign(Active.Importer.custom_launch.encode())
        if Active.Importer.run_post_load:
            Active.Importer.run_post_load_signature = self.security.sign(Active.Importer.run_post_load.encode())
        if Active.Importer.extra_libraries:
            Active.Importer.extra_libraries_signature = self.security.sign(Active.Importer.extra_libraries.encode())
        if save_config:
            Config.save()


Config: AppConfig = AppConfig()
ConfigSecurity: AppConfigSecurity = AppConfigSecurity()

# Config aliases, intended to shorten dot names
Launcher: launcher_package.LauncherManagerConfig
Packages: package_manager.PackageManagerConfig
Importers: ImportersConfig
Active: Union[gimi_package.GIMIPackageConfig, srmi_package.SRMIPackageConfig,
              wwmi_package.WWMIPackageConfig, zzmi_package.ZZMIPackageConfig,
              himi_package.HIMIPackageConfig]


def get_resource_path(element, filename: Union[str, Path], extensions: Optional[Union[str, List[str]]] = None):
    filename = Path(filename)
    search_extensions = [filename.suffix]
    if extensions is not None:
        search_extensions += [ext for ext in list(extensions) if ext != filename.suffix]
    class_path = element.get_resource_path() / filename
    for extension in search_extensions:
        resource_path = Config.theme_path / class_path.with_suffix(extension)
        if resource_path.is_file():
            return resource_path
    resource_path = Paths.App.Themes / 'Default' / class_path
    if not resource_path.is_file():
        raise FileNotFoundError(
            f'Resource not found:\n\n'
            f'{resource_path}\n\n'
            f'Hint: You can also use other extensions: {", ".join(extensions)}')
    return resource_path
