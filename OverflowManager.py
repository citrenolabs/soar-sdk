from os import path
import SiemplifyUtils
import json
import traceback


class OverflowManager(object):
    OVERFLOW_DATA_FILENAME = "overflow.data"
    DIGESTION_TIMES_KEY = 'digestion_times'
    OVERFLOW_DATA_KEY = "overflow_data"
    OVERFLOW_SETTINGS_KEY = "overflow_settings"
    OVERFLOW_SETTINGS_IDENTIFIER = "overflow_settings"
    NOTIFICATION_TIME_KEY = "last_notification_time"

    def __init__(self, logger, is_test_run, manager_cache_folder_path, overflow_manager_settings=None,
                 overflow_manager_config_file_path=None, connector_instance=None):
        self.is_test_run = is_test_run
        self.LOGGER = logger
        self._manager_cache_folder_path = manager_cache_folder_path
        self.reported_overflows = []
        self.connector_instance = connector_instance
        self.connector_identifier = connector_instance.context.connector_info.identifier
        self.DEFAULT_CONFIG_FILE_PATH = path.join(path.dirname(__file__), 'ConnectorsOverflow.config')
        overflow_manager_config_file_path = \
            overflow_manager_config_file_path if overflow_manager_config_file_path else self.DEFAULT_CONFIG_FILE_PATH

        if not overflow_manager_settings and not overflow_manager_config_file_path and connector_instance.sdk_config.is_remote_publisher_sdk:
                    raise Exception(
                        "'overflow_manager_settings' or a valid 'overflow_manager_config_file_path' must be provided")
                                
        if overflow_manager_settings and overflow_manager_config_file_path and connector_instance.sdk_config.is_remote_publisher_sdk:
            raise Exception(
                "Expected only one initlization param 'overflow_manager_settings' or a valid 'overflow_manager_config_file_path', both were provided")
        
        if overflow_manager_settings:
            self._settings = overflow_manager_settings
        else:
            #for local settings record
            if not connector_instance.sdk_config.is_remote_publisher_sdk:
                self._settings = self._load_settings_from_record()
            #for remote settings file
            else:                
                if overflow_manager_config_file_path:
                    if not path.exists(overflow_manager_config_file_path):
                        raise Exception(
                            "overflow_manager_config_file_path {0} doesn't exist".format(overflow_manager_config_file_path))        
                    self._settings = self._load_settings_from_file(overflow_manager_config_file_path)

    @staticmethod
    def _load_settings_from_file(file_path):
        """
        load settings
        :param file_path: {string} settings file path
        :return: OverflowManagerSettings
        """
        f = open(file_path, 'r')
        json_content = f.read()
        manager_json = OverflowManager.version_safe_json_loads(json_content)
        manager_settings = OverflowManagerSettings(**manager_json)
        f.close()

        return manager_settings
        
    def _load_settings_from_record(self):
        """
        load settings
        :return: OverflowManagerSettings
        """
        json_content = self.connector_instance.get_connector_context_property(self.OVERFLOW_SETTINGS_IDENTIFIER, self.OVERFLOW_SETTINGS_KEY)
        manager_json = OverflowManager.version_safe_json_loads(json_content)
        manager_settings = OverflowManagerSettings(**manager_json)
        return manager_settings

    @staticmethod
    def version_safe_json_loads(json_content):
        kwargs = {}
        if json_content == None:
            return {}
        if not SiemplifyUtils.is_python_37():
            kwargs["encoding"] = "utf-8"

        return json.loads(json_content, **kwargs)


    @staticmethod
    def version_safe_json_dumps(jsonable_object):
        kwargs = { "sort_keys" : True,
                   "indent" : 4,
                   "separators" : (',', ': ')}

        if not SiemplifyUtils.is_python_37():
            kwargs["encoding"] = "utf-8"

        return json.dumps(jsonable_object, **kwargs)

    def check_is_alert_overflowed(self, overflow_alert_details):
        """
        Check if alert is overflowed. If yes - report it.
        :param overflow_alert_details:
        :return: {boolean} True if alert is overflowed
        """
        oad = overflow_alert_details

        if not self._settings.is_overflow_enabled:
            return False

        if self.is_test_run:
            self.LOGGER.info("Overflow not checked because this is a test run")
            return False

        if not oad:
            raise Exception("Error check overflow, given overflow_alert_details is empty")

        result = True

        try:
            overflow_cache = self._load_alerts_overflow_cache()
            self._clear_old_alerts_times(overflow_cache)

            alert_identifier = self._build_alert_identifier(oad)

            if alert_identifier not in overflow_cache:
                overflow_cache[alert_identifier] = {self.DIGESTION_TIMES_KEY: [],  # digestions unix times list
                                                    self.NOTIFICATION_TIME_KEY: 0}  # last notification unix time

            current_alert_digestion_times = overflow_cache[alert_identifier][self.DIGESTION_TIMES_KEY]

            # keep it for legacy purposes.
            # until we can unsure no customer code is calling it anymore
            # current_alert_last_notification_time = overflow_cache[alert_identifier][self.NOTIFICATION_TIME_KEY]

            if len(current_alert_digestion_times) < self._settings.max_alerts_in_time_period:
                result = False

            current_alert_digestion_times.append(SiemplifyUtils.unix_now())

            if result:
                # We no longer consider notification time, we just always pass the overflow alert.
                # time_since_last_notificaiton = SiemplifyUtils.unix_now()-current_alert_last_notification_time
                # if (time_since_last_notificaiton>(self._settings.notification_interval_minutes*60*1000)):

                overflow_cache[alert_identifier][self.NOTIFICATION_TIME_KEY] = SiemplifyUtils.unix_now()
                self._report_alert_as_overflow(oad)

            self._save_alerts_overflow_cache(overflow_cache)

        except Exception as e:
            msg = "Error checking overflow, Details: {0} {1}".format(
                            str(e), traceback.format_exc())
            raise Exception(msg)

        return result

    def _load_alerts_overflow_cache(self):
        """
        Open overflow cache and validate last notification_time
        :return: overflow cache data
        """
        try:        
            json_content = self.connector_instance.get_connector_context_property(self.connector_identifier, self.OVERFLOW_DATA_KEY)
            overflow_cache = OverflowManager.version_safe_json_loads(json_content)
        except Exception as e:
            overflow_cache = {}
            self.LOGGER.error("Failed to open or parse Overflow cache with id {}".format(self.connector_identifier))
            self.LOGGER.exception(e)

        # validate last notification_time values:
        for alert_identifier in overflow_cache:
            # Get Last notification time from cache
            if self.NOTIFICATION_TIME_KEY in overflow_cache[alert_identifier]:
                overflow_cache[alert_identifier][self.NOTIFICATION_TIME_KEY] = OverflowManager.version_safe_long_conversion(overflow_cache[alert_identifier][self.NOTIFICATION_TIME_KEY])
            else:
                overflow_cache[alert_identifier][
                    self.NOTIFICATION_TIME_KEY] = 0  # Init when missing, for legacy support

        return overflow_cache


    @staticmethod
    def version_safe_long_conversion(value):
        if SiemplifyUtils.is_python_37():
            return int(value)
        else:
            return long(value)

    def _save_alerts_overflow_cache(self, alert_times):
        """
        Save cache
        :param alert_times: {dict} overflow cache
        """
        json_content = OverflowManager.version_safe_json_dumps(alert_times)
        self.connector_instance.set_connector_context_property(self.connector_identifier, self.OVERFLOW_DATA_KEY, json_content)

    def _clear_old_alerts_times(self, overflow_cache):
        """
        remove old alerts
        :param overflow_cache: {dict} overflow cache
        """
        for alert_identifier in overflow_cache:
            times_to_remove = []
            current_alert_digestion_times = overflow_cache[alert_identifier][self.DIGESTION_TIMES_KEY]
            for timestamp in current_alert_digestion_times:
                time_passed_minutes = (SiemplifyUtils.unix_now() - timestamp) / float(1000) / float(60)
                if time_passed_minutes > self._settings.time_period_in_min:
                    times_to_remove.append(timestamp)

            for overdue_time in times_to_remove:
                current_alert_digestion_times.remove(overdue_time)

    def _build_alert_identifier(self, overflow_alert_details):
        """
        create alert overflow key
        :param overflow_alert_details: (overflow settings - environment, product, etc)
        :return: {string} alert key
        """
        oad = overflow_alert_details
        result = ""

        if self._settings.is_environment_considered:
            result += "|" + oad.environment

        if self._settings.is_product_considered:
            product = ""
            if oad.product:
                product = oad.product
            result += "|" + product

        if self._settings.is_rule_generator_considered:
            result += "|" + oad.alert_name

        return result

    def _report_alert_as_overflow(self, overflow_alert_details):
        """
        report alert as overflow
        :param overflow_alert_details: (overflow settings - environment, product, etc)
        """

        # We need to convert to pascal case for C# json serialier:
        # save_content = overflow_alert_details.return_pascal_case_dictionary()
        # json_content = json.dumps(save_content, sort_keys=True, indent=4, separators=(',', ': '))
        # self.reported_overflows.append(save_content)

        self.reported_overflows.append(overflow_alert_details)


class OverflowManagerSettings(object):
    def __init__(self, is_overflow_enabled=True, is_environment_considered=True, is_product_considered=True,
                 is_rule_generator_considered=True, max_alerts_in_time_period=50, time_period_in_min=10):
        self.is_overflow_enabled = is_overflow_enabled
        self.is_environment_considered = is_environment_considered
        self.is_product_considered = is_product_considered
        self.is_rule_generator_considered = is_rule_generator_considered
        self.max_alerts_in_time_period = max_alerts_in_time_period
        self.time_period_in_min = time_period_in_min


class OverflowAlertDetails(object):
    def __init__(self, environment, source_system_name, connector_identifier, original_file_path, original_file_content,
                 ingestion_time, alert_identifier, alert_name=None, product=None, source_ip=None, source_host=None,
                 destination_ip=None, destination_host=None, siem_alert_id=None, source_system_url=None,
                 source_rule_identifier=None):
        self.environment = self.empty_if_none(environment)
        self.source_system_name = self.empty_if_none(source_system_name)
        self.connector_identifier = self.empty_if_none(connector_identifier)
        self.original_file_path = self.empty_if_none(original_file_path)
        self.original_file_content = self.empty_if_none(original_file_content)
        self.ingestion_time = ingestion_time
        self.alert_identifier = self.empty_if_none(alert_identifier)
        self.alert_name = self.empty_if_none(alert_name)
        self.product = self.empty_if_none(product)
        self.source_ip = self.empty_if_none(source_ip)
        self.source_host = self.empty_if_none(source_host)
        self.destination_ip = self.empty_if_none(destination_ip)
        self.destination_host = self.empty_if_none(destination_host)
        self.siem_alert_id = self.empty_if_none(siem_alert_id)
        self.source_system_url = self.empty_if_none(source_system_url)
        self.source_rule_identifier = self.empty_if_none(source_rule_identifier)

        if not ingestion_time:
            raise Exception("ingestion_time cannot be None")

    @staticmethod
    def empty_if_none(s):
        if s is None:
            return ""
        return str(s)
