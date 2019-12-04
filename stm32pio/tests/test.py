"""
'pyenv' is recommended to use for testing with different Python versions
https://www.tecmint.com/pyenv-install-and-manage-multiple-python-versions-in-linux/

To get test coverage use 'coverage':
    $  coverage run -m stm32pio.tests.test -b
    $  coverage html
"""
import logging
import re
import unittest
import configparser
import pathlib
import platform
import shutil
import subprocess
import tempfile
import time
import inspect
import sys

import stm32pio.app
import stm32pio.settings
import stm32pio.lib


STM32PIO_MAIN_SCRIPT = inspect.getfile(stm32pio.app)  # absolute path to the main stm32pio script
# absolute path to the Python executable (no need to guess whether it's python or python3 and so on)
PYTHON_EXEC = sys.executable

# Test data
TEST_PROJECT_PATH = pathlib.Path('stm32pio-test-project').resolve()
if not TEST_PROJECT_PATH.is_dir() or not TEST_PROJECT_PATH.joinpath('stm32pio-test-project.ioc').is_file():
    raise FileNotFoundError("No test project is present")
# Make sure you have F0 framework installed (try to run code generation from STM32CubeMX manually at least once before
# proceeding)
TEST_PROJECT_BOARD = 'nucleo_f031k6'

# Instantiate a temporary folder on every fixture run. It is used across all tests and is deleted on shutdown
temp_dir = tempfile.TemporaryDirectory()
FIXTURE_PATH = pathlib.Path(temp_dir.name).joinpath(TEST_PROJECT_PATH.name)
print(f"Temp test fixture path: {FIXTURE_PATH}")



class CustomTestCase(unittest.TestCase):
    """
    These pre- and post-tasks are common for all test cases
    """

    def setUp(self):
        """
        Copy the test project from the repo to our temp directory. WARNING: make sure the test project folder is clean
        (i.e. contains only an .ioc file) before running the test
        """
        shutil.rmtree(FIXTURE_PATH, ignore_errors=True)
        shutil.copytree(TEST_PROJECT_PATH, FIXTURE_PATH)

    def tearDown(self):
        """
        Clean the temp directory
        """
        shutil.rmtree(FIXTURE_PATH, ignore_errors=True)


class TestUnit(CustomTestCase):
    """
    Test the single method. As we at some point decided to use a class instead of the set of scattered functions we need
    to do some preparations for almost every test (e.g. instantiate the class, create the PlatformIO project, etc.),
    though
    """

    def test_generate_code(self):
        """
        Check whether files and folders have been created by STM32CubeMX
        """
        project = stm32pio.lib.Stm32pio(FIXTURE_PATH, parameters={'board': TEST_PROJECT_BOARD},
                                        save_on_destruction=False)
        project.generate_code()

        # Assuming that the presence of these files indicating a success
        files_should_be_present = ['Src/main.c', 'Inc/main.h']
        for file in files_should_be_present:
            with self.subTest(file_should_be_present=file, msg=f"{file} hasn't been created"):
                self.assertEqual(FIXTURE_PATH.joinpath(file).is_file(), True)

    def test_pio_init(self):
        """
        Consider that existence of 'platformio.ini' file showing a successful PlatformIO project initialization. The
        last one has another traces those can be checked too but we are interested only in a 'platformio.ini' anyway
        """
        project = stm32pio.lib.Stm32pio(FIXTURE_PATH, parameters={'board': TEST_PROJECT_BOARD},
                                        save_on_destruction=False)
        result = project.pio_init()

        self.assertEqual(result, 0, msg="Non-zero return code")
        self.assertTrue(FIXTURE_PATH.joinpath('platformio.ini').is_file(), msg="platformio.ini is not there")
        # TODO: check that platformio.ini is a correct configparser file and is not empty

    def test_patch(self):
        """
        Compare contents of the patched string and the patch itself
        """
        test_content = "*** TEST PLATFORMIO.INI FILE ***"
        FIXTURE_PATH.joinpath('platformio.ini').write_text(test_content)

        project = stm32pio.lib.Stm32pio(FIXTURE_PATH, save_on_destruction=False)
        project.patch()

        self.assertFalse(FIXTURE_PATH.joinpath('include').is_dir(), msg="'include' has not been deleted")

        after_patch_content = FIXTURE_PATH.joinpath('platformio.ini').read_text()

        self.assertEqual(after_patch_content[:len(test_content)], test_content,
                         msg="Initial content of platformio.ini is corrupted")
        self.assertEqual(after_patch_content[len(test_content):],
                         stm32pio.settings.config_default['project']['platformio_ini_patch_content'],
                         msg="Patch content is not as expected")

    def test_build_should_handle_error(self):
        """
        Build an empty project so PlatformIO should return the error
        """
        project = stm32pio.lib.Stm32pio(FIXTURE_PATH, parameters={'board': TEST_PROJECT_BOARD},
                                        save_on_destruction=False)
        project.pio_init()

        with self.assertLogs(level='ERROR') as logs:
            self.assertNotEqual(project.pio_build(), 0, msg="Build error was not indicated")
            self.assertTrue(next((True for item in logs.output if "PlatformIO build error" in item), False),
                            msg="Error message does not match")

    def test_run_editor(self):
        """
        Call the editors
        """
        project = stm32pio.lib.Stm32pio(FIXTURE_PATH, save_on_destruction=False)

        editors = {
            'atom': {
                'Windows': 'atom.exe',
                'Darwin': 'Atom',
                'Linux': 'atom'
            },
            'code': {
                'Windows': 'Code.exe',
                'Darwin': 'Visual Studio Code',
                'Linux': 'code'
            },
            'subl': {
                'Windows': 'sublime_text.exe',
                'Darwin': 'Sublime',
                'Linux': 'sublime'
            }
        }

        for command, name in editors.items():
            # Look for the command presence in the system so we test only installed editors
            if platform.system() == 'Windows':
                command_str = f"where {command} /q"
            else:
                command_str = f"command -v {command}"
            editor_exists = False
            if subprocess.run(command_str, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0:
                editor_exists = True

            if editor_exists:
                with self.subTest(command=command, name=name[platform.system()]):
                    project.start_editor(command)

                    time.sleep(1)  # wait a little bit for app to start

                    command_arr = ['ps', '-A']
                    if platform.system() == 'Windows':
                        command_arr = ['wmic', 'process', 'get', 'description']
                    # "encoding='utf-8'" is for "a bytes-like object is required, not 'str'" in "assertIn"
                    result = subprocess.run(command_arr, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            encoding='utf-8')
                    # Or, for Python 3.7 and above:
                    # result = subprocess.run(command_arr, capture_output=True, encoding='utf-8')
                    self.assertIn(name[platform.system()], result.stdout)

    def test_init_path_not_found_should_raise(self):
        """
        Pass non-existing path and expect the error
        """
        path_does_not_exist_name = 'does_not_exist'

        path_does_not_exist = FIXTURE_PATH.joinpath(path_does_not_exist_name)
        with self.assertRaisesRegex(FileNotFoundError, path_does_not_exist_name,
                                    msg="FileNotFoundError was not raised or doesn't contain a description"):
            stm32pio.lib.Stm32pio(path_does_not_exist, save_on_destruction=False)

    def test_save_config(self):
        """
        Explicitly save the config to file and look did that actually happen and whether all the information was
        preserved
        """
        # 'board' is non-default, 'project'-section parameter
        project = stm32pio.lib.Stm32pio(FIXTURE_PATH, parameters={'board': TEST_PROJECT_BOARD},
                                        save_on_destruction=False)

        project.save_config()

        self.assertTrue(FIXTURE_PATH.joinpath(stm32pio.settings.config_file_name).is_file(),
                        msg=f"{stm32pio.settings.config_file_name} file hasn't been created")

        config = configparser.ConfigParser()
        config.read(str(FIXTURE_PATH.joinpath(stm32pio.settings.config_file_name)))
        for section, parameters in stm32pio.settings.config_default.items():
            for option, value in parameters.items():
                with self.subTest(section=section, option=option, msg="Section/key is not found in saved config file"):
                    self.assertNotEqual(config.get(section, option, fallback="Not found"), "Not found")
        self.assertEqual(config.get('project', 'board', fallback="Not found"), TEST_PROJECT_BOARD,
                         msg="'board' has not been set")


class TestIntegration(CustomTestCase):
    """
    Sequence of methods that should work seamlessly
    """

    def test_config_priorities(self):
        """
        Test the compliance with priorities when reading the parameters
        """

        config_parameter_user_value = "SOME CUSTOM CONTENT"
        cli_parameter_user_value = 'nucleo_f429zi'

        # Create test config
        config = configparser.ConfigParser()
        config.read_dict({
            'project': {
                'platformio_ini_patch_content': config_parameter_user_value,
                'board': TEST_PROJECT_BOARD
            }
        })
        # ... save it
        with FIXTURE_PATH.joinpath(stm32pio.settings.config_file_name).open(mode='w') as config_file:
            config.write(config_file)

        # On project creation we should interpret the CLI-provided values as superseding to the saved ones and
        # saved ones as superseding to the default ones
        project = stm32pio.lib.Stm32pio(FIXTURE_PATH, parameters={'board': cli_parameter_user_value},
                                        save_on_destruction=False)
        project.pio_init()
        project.patch()

        # Actually, we can parse platformio.ini via configparser but this is simpler
        after_patch_content = FIXTURE_PATH.joinpath('platformio.ini').read_text()
        self.assertIn(config_parameter_user_value, after_patch_content,
                      msg="User config parameter has not been prioritized over the default one")
        self.assertIn(cli_parameter_user_value, after_patch_content,
                      msg="User CLI parameter has not been prioritized over the saved one")

    def test_build(self):
        """
        Initialize a new project and try to build it
        """
        project = stm32pio.lib.Stm32pio(FIXTURE_PATH, parameters={'board': TEST_PROJECT_BOARD},
                                        save_on_destruction=False)
        project.generate_code()
        project.pio_init()
        project.patch()

        result = project.pio_build()

        self.assertEqual(result, 0, msg="Build failed")

    def test_regenerate_code(self):
        """
        Simulate a new project creation, its changing and CubeMX code re-generation (for example, after adding new
        hardware features and some new files by a user)
        """
        project = stm32pio.lib.Stm32pio(FIXTURE_PATH, parameters={'board': TEST_PROJECT_BOARD},
                                        save_on_destruction=False)

        # Generate a new project ...
        project.generate_code()
        project.pio_init()
        project.patch()

        # ... change it:
        test_file_1 = FIXTURE_PATH.joinpath('Src', 'main.c')
        test_content_1 = "*** TEST STRING 1 ***\n"
        test_file_2 = FIXTURE_PATH.joinpath('Inc', 'my_header.h')
        test_content_2 = "*** TEST STRING 2 ***\n"
        #   - add some sample string inside CubeMX' /* BEGIN - END */ block
        main_c_content = test_file_1.read_text()
        pos = main_c_content.index("while (1)")
        main_c_new_content = main_c_content[:pos] + test_content_1 + main_c_content[pos:]
        test_file_1.write_text(main_c_new_content)
        #  - add new file inside the project
        test_file_2.write_text(test_content_2)

        # Re-generate CubeMX project
        project.generate_code()

        # Check if added information is preserved
        main_c_after_regenerate_content = test_file_1.read_text()
        my_header_h_after_regenerate_content = test_file_2.read_text()
        self.assertIn(test_content_1, main_c_after_regenerate_content,
                      msg=f"User content hasn't been preserved after regeneration in {test_file_1}")
        self.assertIn(test_content_2, my_header_h_after_regenerate_content,
                      msg=f"User content hasn't been preserved after regeneration in {test_file_2}")


class TestCLI(CustomTestCase):
    """
    Some tests to mimic the behavior of end-user tasks (CLI commands such as 'new', 'clean', etc.). Run main function
    passing the arguments to it but sometimes even run as subprocess (to capture actual STDOUT/STDERR output)
    """

    def test_clean(self):
        # Create files and folders
        file_should_be_deleted = FIXTURE_PATH.joinpath('file.should.be.deleted')
        dir_should_be_deleted = FIXTURE_PATH.joinpath('dir.should.be.deleted')
        file_should_be_deleted.touch(exist_ok=False)
        dir_should_be_deleted.mkdir(exist_ok=False)

        # Clean
        return_code = stm32pio.app.main(sys_argv=['clean', '-d', str(FIXTURE_PATH)])
        self.assertEqual(return_code, 0, msg="Non-zero return code")

        # Look for remaining items
        self.assertFalse(file_should_be_deleted.is_file(), msg=f"{file_should_be_deleted} is still there")
        self.assertFalse(dir_should_be_deleted.is_dir(), msg=f"{dir_should_be_deleted} is still there")

        # And .ioc file should be preserved
        self.assertTrue(FIXTURE_PATH.joinpath(f"{FIXTURE_PATH.name}.ioc").is_file(), msg="Missing .ioc file")

    def test_new(self):
        """
        Successful build is the best indicator that all went right so we use '--with-build' option
        """

        return_code = stm32pio.app.main(sys_argv=['new', '-d', str(FIXTURE_PATH), '-b', TEST_PROJECT_BOARD,
                                                  '--with-build'])
        self.assertEqual(return_code, 0, msg="Non-zero return code")

        # .ioc file should be preserved
        self.assertTrue(FIXTURE_PATH.joinpath(f"{FIXTURE_PATH.name}.ioc").is_file(), msg="Missing .ioc file")

    def test_generate(self):
        return_code = stm32pio.app.main(sys_argv=['generate', '-d', str(FIXTURE_PATH)])
        self.assertEqual(return_code, 0, msg="Non-zero return code")

        inc_dir = 'Inc'
        src_dir = 'Src'

        self.assertTrue(FIXTURE_PATH.joinpath(inc_dir).is_dir(), msg=f"Missing '{inc_dir}'")
        self.assertTrue(FIXTURE_PATH.joinpath(src_dir).is_dir(), msg=f"Missing '{src_dir}'")
        self.assertFalse(len(list(FIXTURE_PATH.joinpath(inc_dir).iterdir())) == 0, msg=f"'{inc_dir}' is empty")
        self.assertFalse(len(list(FIXTURE_PATH.joinpath(src_dir).iterdir())) == 0, msg=f"'{src_dir}' is empty")

        # .ioc file should be preserved
        self.assertTrue(FIXTURE_PATH.joinpath(f"{FIXTURE_PATH.name}.ioc").is_file(), msg="Missing .ioc file")

    def test_incorrect_path_should_log_error(self):
        """
        We should see an error log message and non-zero return code
        """

        path_not_exist = pathlib.Path('path/does/not/exist')

        with self.assertLogs(level='ERROR') as logs:
            return_code = stm32pio.app.main(sys_argv=['init', '-d', str(path_not_exist)])
            self.assertNotEqual(return_code, 0, msg='Return code should be non-zero')
            self.assertTrue(next((True for item in logs.output if str(path_not_exist) in item), False),
                            msg="'ERROR' logging message hasn't been printed")

    def test_no_ioc_file_should_log_error(self):
        """
        We should see an error log message and non-zero return code
        """

        dir_with_no_ioc_file = FIXTURE_PATH.joinpath('dir.with.no.ioc.file')
        dir_with_no_ioc_file.mkdir(exist_ok=False)

        with self.assertLogs(level='ERROR') as logs:
            return_code = stm32pio.app.main(sys_argv=['init', '-d', str(dir_with_no_ioc_file)])
            self.assertNotEqual(return_code, 0, msg='Return code should be non-zero')
            self.assertTrue(next((True for item in logs.output if "CubeMX project .ioc file" in item), False),
                            msg="'ERROR' logging message hasn't been printed")

    def test_logs_format(self):
        logger = logging.getLogger('stm32pio')
        handler = logging.StreamHandler()
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(levelname)-8s %(funcName)-26s %(message)s"))

        with self.assertLogs(level='DEBUG') as logs:
            return_code = stm32pio.app.main(sys_argv=['-v', 'new', '-d', str(FIXTURE_PATH), '-b', TEST_PROJECT_BOARD,
                                                      '--with-build'])
            print('\n\n\n\n' + '\n\n'.join(logs.output))
            # print([entry.msg for entry in logs.records])
            self.assertEqual(return_code, 0, msg="Non-zero return code")
            # self.assertRegex(logs.)

        # with self.assertLogs(level='INFO') as logs:
        #     return_code = stm32pio.app.main(sys_argv=['new', '-d', str(FIXTURE_PATH), '-b', TEST_PROJECT_BOARD,
        #                                               '--with-build'])
        #     self.assertEqual(return_code, 0, msg="Non-zero return code")

    # TODO: test logs format
    # non-verbose should satisfy:
    #     ^(?=(INFO) {0,4})(?=.{8} ((?!( |pio_build|pio_init))))
    #
    # verbose:
    #     ^(?=(DEBUG|INFO|WARNING|ERROR|CRITICAL) {0,4})(?=.{8} (?=(pio_build|pio_init) {0,26})(?=.{26} [^ ]))
    #
    # we can actually get possible function names in that 26-width block

    def test_verbose(self):
        """
        Run as subprocess to capture the full output. Check for both 'DEBUG' logging messages and STM32CubeMX CLI output
        """

        project = stm32pio.lib.Stm32pio(FIXTURE_PATH, save_on_destruction=False)
        methods = [method[0] for method in inspect.getmembers(project, predicate=inspect.ismethod)]
        methods.append('main')

        result = subprocess.run([PYTHON_EXEC, STM32PIO_MAIN_SCRIPT, '-v', 'generate', '-d', str(FIXTURE_PATH)],
                                encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        self.assertEqual(result.returncode, 0, msg="Non-zero return code")
        # Somehow stderr and not stdout contains the actual output but we check both
        self.assertTrue('DEBUG' in result.stderr or 'DEBUG' in result.stdout,
                        msg="Verbose logging output hasn't been enabled on stderr")
        regex = re.compile("^(?=(DEBUG) {0,4})(?=.{8} (?=(" +
                           '|'.join(methods) +
                           ") {0,26})(?=.{26} [^ ]))", flags=re.MULTILINE)
        self.assertGreaterEqual(len(re.findall(regex, result.stderr)), 1,
                                msg="Logs messages doesn't match the format")

        self.assertIn('Starting STM32CubeMX', result.stdout, msg="STM32CubeMX didn't print its logs")

    def test_non_verbose(self):
        """
        Run as subprocess to capture the full output. We should not see any 'DEBUG' logging messages or STM32CubeMX CLI
        output
        """

        project = stm32pio.lib.Stm32pio(FIXTURE_PATH, save_on_destruction=False)
        methods = [method[0] for method in inspect.getmembers(project, predicate=inspect.ismethod)]
        methods.append('main')

        result = subprocess.run([PYTHON_EXEC, STM32PIO_MAIN_SCRIPT, 'generate', '-d', str(FIXTURE_PATH)],
                                encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        self.assertEqual(result.returncode, 0, msg="Non-zero return code")
        self.assertNotIn('DEBUG', result.stderr, msg="Verbose logging output has been enabled on stderr")
        self.assertNotIn('DEBUG', result.stdout, msg="Verbose logging output has been enabled on stdout")

        regex = re.compile("^(?=(INFO) {0,4})(?=.{8} ((?!( |" +
                           '|'.join(methods) +
                           "))))", flags=re.MULTILINE)
        self.assertGreaterEqual(len(re.findall(regex, result.stderr)), 1,
                                msg="Logs messages doesn't match the format")

        self.assertNotIn('Starting STM32CubeMX', result.stdout, msg="STM32CubeMX printed its logs")

    def test_init(self):
        """
        Check for config creation and parameters presence
        """

        result = subprocess.run([PYTHON_EXEC, STM32PIO_MAIN_SCRIPT, 'init', '-d', str(FIXTURE_PATH),
                                 '-b', TEST_PROJECT_BOARD])
        self.assertEqual(result.returncode, 0, msg="Non-zero return code")

        self.assertTrue(FIXTURE_PATH.joinpath(stm32pio.settings.config_file_name).is_file(),
                        msg=f"{stm32pio.settings.config_file_name} file hasn't been created")

        config = configparser.ConfigParser()
        config.read(str(FIXTURE_PATH.joinpath(stm32pio.settings.config_file_name)))
        for section, parameters in stm32pio.settings.config_default.items():
            for option, value in parameters.items():
                with self.subTest(section=section, option=option, msg="Section/key is not found in saved config file"):
                    self.assertIsNotNone(config.get(section, option, fallback=None))
        self.assertEqual(config.get('project', 'board', fallback="Not found"), TEST_PROJECT_BOARD,
                         msg="'board' has not been set")


if __name__ == '__main__':
    unittest.main()
