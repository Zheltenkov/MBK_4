<?php
// Thin lab adapter. All lead/UF/FM/address writes below use original native APIs.
function native_actor_id(): int
{
    $actor = Bitrix\Main\UserTable::getList(['filter' => ['=LOGIN' => getenv('MBK_NATIVE_ADMIN_USER')], 'select' => ['ID']])->fetch();
    if (!$actor) {
        throw new RuntimeException('Local native administrator was not installed');
    }
    return (int)$actor['ID'];
}

function native_value($value)
{
    if (is_array($value)) {
        return array_map('native_value', $value);
    }
    return is_object($value) && method_exists($value, '__toString') ? (string)$value : $value;
}

function native_monthly_value(array $input): ?float
{
    $value = $input['UF_GENERAL_MONTHLY_PAYMENTS'] ?? null;
    // The actual site mapper emits null for a zero/empty payment; preserve its unknown state.
    if ($value === null || $value === '') {
        return null;
    }
    if (!is_numeric($value) || (float)$value !== 0.0) {
        throw new InvalidArgumentException('Unsupported synthetic monthly payment');
    }
    return (float)$value;
}

function native_read(int $id): array
{
    global $USER_FIELD_MANAGER;
    $lead = Bitrix\Crm\LeadTable::getList(['filter' => ['=ID' => $id],
        'select' => ['ID', 'TITLE', 'STATUS_ID', 'SOURCE_ID', 'ORIGINATOR_ID', 'ORIGIN_ID', 'CURRENCY_ID', 'OPPORTUNITY', 'BIRTHDATE']])->fetch();
    if (!$lead || $lead['ORIGINATOR_ID'] !== 'local_native_harness' || !str_starts_with((string)$lead['ORIGIN_ID'], 'local-write-native')) {
        throw new OutOfBoundsException('No matching native fixture lead');
    }
    $birth = $lead['BIRTHDATE'];
    $lead['native_birthdate_iso'] = $birth instanceof Bitrix\Main\Type\Date ? $birth->format('Y-m-d') : null;
    $lead['BIRTHDATE'] = native_value($birth);
    // UTM fields are separate native entities, not physical lead columns or scalar references.
    $lead['UTM_SOURCE'] = null;
    $lead['UTM_CAMPAIGN'] = null;
    $utm = Bitrix\Crm\UtmTable::getList(['filter' => ['=ENTITY_TYPE_ID' => CCrmOwnerType::Lead, '=ENTITY_ID' => $id], 'select' => ['CODE', 'VALUE']]);
    while ($item = $utm->fetch()) {
        if (in_array($item['CODE'], ['UTM_SOURCE', 'UTM_CAMPAIGN'], true)) {
            $lead[$item['CODE']] = $item['VALUE'];
        }
    }
    $fields = $USER_FIELD_MANAGER->GetUserFields('CRM_LEAD', $id, 'ru');
    foreach (native_definitions() as $name => $definition) {
        if (!isset($fields[$name])) {
            throw new RuntimeException('Native user field read is missing');
        }
        $lead[$name] = native_value($fields[$name]['VALUE']);
    }
    $phoneRows = Bitrix\Crm\FieldMultiTable::getList(['filter' => ['=ENTITY_ID' => 'LEAD', '=ELEMENT_ID' => $id,
        '=TYPE_ID' => 'PHONE', '=VALUE_TYPE' => 'WORK'], 'select' => ['ID', 'VALUE']])->fetchAll();
    $address = Bitrix\Crm\AddressTable::getList(['filter' => ['=ENTITY_TYPE_ID' => CCrmOwnerType::Lead,
        '=ENTITY_ID' => $id, '=TYPE_ID' => Bitrix\Crm\EntityAddressType::Primary], 'select' => ['PROVINCE']])->fetch();
    $lead['native_phone_work_count'] = count($phoneRows);
    $lead['native_phone_sha256'] = count($phoneRows) === 1 ? hash('sha256', preg_replace('/\D/', '', $phoneRows[0]['VALUE'])) : null;
    $lead['native_address_province'] = $address ? $address['PROVINCE'] : null;
    $lead['native_uf_types'] = array_map(static fn($field) => $field['USER_TYPE_ID'], array_intersect_key($fields, native_definitions()));
    return $lead;
}

function native_provenance(): array
{
    $out = [];
    foreach (['CCrmLead', Bitrix\Crm\LeadTable::class, 'CUserTypeEntity', 'CUserFieldEnum', 'CUserTypeManager', Bitrix\Crm\AddressTable::class, Bitrix\Crm\FieldMultiTable::class, Bitrix\Crm\UtmTable::class] as $class) {
        $reflection = $class === 'CCrmLead' ? new ReflectionMethod($class, 'Add') : new ReflectionClass($class);
        $file = $reflection->getFileName();
        $out[$class] = ['file' => $file, 'sha256' => hash_file('sha256', $file)];
    }
    return $out;
}

function native_assert_saved(array $read, array $input, array $metadata, string $phone, string $hash): void
{
    $expected = ['UF_CRM_1452152621' => $phone,
        'UF_CRM_1452153725' => $input['UF_CRM_1452153725'], 'UF_CRM_1452153749' => $input['UF_CRM_1452153749'],
        'UF_CRM_1452154548' => $metadata['UF_CRM_1452154548']['enums']['local_bfl'],
        'UF_SITE_AGREE_PARTNERS' => $metadata['UF_SITE_AGREE_PARTNERS']['enums']['local_yes'],
        'UF_CRM_1491296976' => $metadata['UF_CRM_1491296976']['enums']['local_bfl_moscow'],
        'UF_CREDIT_HISTORY' => $metadata['UF_CREDIT_HISTORY']['enums']['local_overdue'],
        'ORIGIN_ID' => $input['UTM_CAMPAIGN'], 'SOURCE_ID' => (string)($input['SOURCE_ID'] ?? 'INET')];
    foreach ($expected as $name => $value) {
        if ((string)($read[$name] ?? '') !== (string)$value) {
            throw new RuntimeException('Native immutable postcondition failed: ' . $name);
        }
    }
    if (!hash_equals($hash, (string)$read['UF_LOCAL_NATIVE_PAYLOAD']) || (float)$read['UF_DEBT_SUMM'] !== (float)$input['UF_DEBT_SUMM']
        || (float)$read['OPPORTUNITY'] !== (float)$input['SUMM'] || $read['CURRENCY_ID'] !== 'RUB'
        || $read['native_phone_work_count'] !== 1 || !hash_equals(hash('sha256', $phone), (string)$read['native_phone_sha256'])
        || $read['native_address_province'] !== $input['ADDRESS_PROVINCE']) {
        throw new RuntimeException('Native complete post-write proof failed');
    }
    $monthly = native_monthly_value($input);
    $storedMonthly = $read['UF_GENERAL_MONTHLY_PAYMENTS'];
    if (($monthly === null && $storedMonthly !== null && $storedMonthly !== '')
        || ($monthly !== null && (!is_numeric($storedMonthly) || (float)$storedMonthly !== $monthly))) {
        throw new RuntimeException('Native nullable monthly payment postcondition failed');
    }
    $format = str_contains((string)$input['BIRTHDATE'], '.') ? 'd.m.Y' : 'd-m-Y';
    $birth = DateTimeImmutable::createFromFormat('!' . $format, (string)$input['BIRTHDATE']);
    if (!$birth || $read['native_birthdate_iso'] !== $birth->format('Y-m-d')) {
        throw new RuntimeException('Native birth date postcondition failed');
    }
    if (array_key_exists('UF_TOTAL_DEBT_OTHER', $input)
        && (!is_numeric($read['UF_TOTAL_DEBT_OTHER']) || (float)$read['UF_TOTAL_DEBT_OTHER'] !== (float)$input['UF_TOTAL_DEBT_OTHER'])) {
        throw new RuntimeException('Native other debt postcondition failed');
    }
    if ((array_key_exists('UTMSOURCE', $input) || array_key_exists('UTM_SOURCE', $input))
        && $read['UTM_SOURCE'] !== (string)($input['UTMSOURCE'] ?? $input['UTM_SOURCE'])) {
        throw new RuntimeException('Native UTM source postcondition failed');
    }
    // Shipment fields are mutable and may change after a successfully delivered replay.
}

function native_accept(array $input): array
{
    $operation = $input['UTM_CAMPAIGN'] ?? null;
    $phone = preg_replace('/\D/', '', (string)($input['PHONE'] ?? ''));
    $category = $input['UF_CRM_1491296976'] ?? null;
    if (!is_string($operation) || !preg_match('/^local-write-native[A-Za-z0-9_-]{1,90}$/', $operation)
        || !preg_match('/^[78]000\d{7}$/', $phone)
        || !in_array($input['IS_BFL_PRODUCT'] ?? null, [true, '1'], true)
        || !in_array($input['RETURN_RESPONSE'] ?? null, [true, '1'], true)
        || !is_numeric($input['UF_DEBT_SUMM'] ?? null) || (float)$input['UF_DEBT_SUMM'] !== 400000.0
        || !in_array($category, [3895, '3895'], true)
        || !in_array($input['UF_SITE_AGREE_PARTNERS'] ?? null, [23000, '23000'], true)
        || !in_array($input['UF_CREDIT_HISTORY'] ?? null, [9257, '9257'], true)
        || preg_replace('/\D/', '', (string)($input['PHONE_DEAL'] ?? '')) !== $phone
        || !in_array($input['SUMM'] ?? null, [0, '0'], true)
        || ($input['ADDRESS_PROVINCE'] ?? null) !== 'Москва'
        || ($input['UF_CRM_1452153725'] ?? null) !== 'Москва'
        || ($input['UF_CRM_1452153749'] ?? null) !== 'Москва'
        || ($input['STATUS_ID'] ?? null) !== 'RESTORED') {
        throw new InvalidArgumentException('Unsupported synthetic native fixture');
    }
    foreach ($input as $key => $value) {
        if (!is_string($key) || (!is_scalar($value) && $value !== null)) {
            throw new InvalidArgumentException('Native input must contain scalar legacy fields');
        }
    }
    $monthly = native_monthly_value($input);
    // The fixture supplies a valid adult birthday; this does not introduce a BFL age rule.
    $birthText = $input['BIRTHDATE'] ?? null;
    $format = is_string($birthText) && str_contains($birthText, '.') ? 'd.m.Y' : 'd-m-Y';
    $birth = is_string($birthText) ? DateTimeImmutable::createFromFormat('!' . $format, $birthText) : false;
    if (!$birth || $birth->format($format) !== $birthText) {
        throw new InvalidArgumentException('Missing or invalid synthetic birth date');
    }
    $canonical = $input;
    ksort($canonical, SORT_STRING);
    $hash = hash('sha256', json_encode($canonical, JSON_UNESCAPED_UNICODE | JSON_PRESERVE_ZERO_FRACTION | JSON_THROW_ON_ERROR));
    $metadata = native_metadata(false);
    $connection = Bitrix\Main\Application::getConnection();
    $lock = 'local_native_' . hash('sha256', $operation);
    if (!$connection->lock($lock, 10)) {
        throw new RuntimeException('Native operation lock unavailable');
    }
    try {
        $previous = Bitrix\Crm\LeadTable::getList(['filter' => ['=ORIGINATOR_ID' => 'local_native_harness', '=ORIGIN_ID' => $operation], 'select' => ['ID']])->fetch();
        if ($previous) {
            $read = native_read((int)$previous['ID']);
            if (!hash_equals($hash, (string)$read['UF_LOCAL_NATIVE_PAYLOAD'])) {
                throw new DomainException('Native operation input conflict');
            }
            native_assert_saved($read, $input, $metadata, $phone, $hash);
            return ['leadID' => (string)$previous['ID'], 'created' => false];
        }
        $enum = static fn(string $field, string $xml): int => $metadata[$field]['enums'][$xml];
        $source = (string)($input['SOURCE_ID'] ?? 'INET');
        if (!preg_match('/^[A-Za-z0-9_]{1,40}$/', $source)) {
            throw new InvalidArgumentException('Invalid native source fixture');
        }
        if (!CCrmStatus::GetList([], ['ENTITY_ID' => 'SOURCE', 'STATUS_ID' => $source])->Fetch()) {
            if (!(new CCrmStatus('SOURCE'))->Add(['STATUS_ID' => $source, 'NAME' => 'Local synthetic source', 'SORT' => 500, 'SYSTEM' => 'N'])) {
                throw new RuntimeException('Native source metadata creation failed');
            }
        }
        $fields = ['TITLE' => 'Local native BFL fixture', 'NAME' => (string)($input['NAME'] ?? 'Local'),
            'LAST_NAME' => (string)($input['LAST_NAME'] ?? 'Synthetic'), 'STATUS_ID' => $input['STATUS_ID'],
            'SOURCE_ID' => $source, 'CURRENCY_ID' => 'RUB', 'OPPORTUNITY' => (float)$input['SUMM'],
            'ASSIGNED_BY_ID' => native_actor_id(), 'ORIGINATOR_ID' => 'local_native_harness', 'ORIGIN_ID' => $operation,
            'UTM_CAMPAIGN' => $operation, 'UTM_SOURCE' => (string)($input['UTMSOURCE'] ?? $input['UTM_SOURCE'] ?? 'synthetic_local_fixture'),
            'FM' => ['PHONE' => ['n0' => ['VALUE' => $phone, 'VALUE_TYPE' => 'WORK']]],
            'UF_CRM_1452152621' => $phone, 'UF_DEBT_SUMM' => (float)$input['UF_DEBT_SUMM'],
            'UF_GENERAL_MONTHLY_PAYMENTS' => $monthly, 'UF_SHIPPED_PARTNER' => 0, 'UF_PARTNER_NAME' => [],
            'UF_CRM_1452154548' => $enum('UF_CRM_1452154548', 'local_bfl'),
            'UF_SITE_AGREE_PARTNERS' => $enum('UF_SITE_AGREE_PARTNERS', 'local_yes'),
            'UF_CRM_1491296976' => $enum('UF_CRM_1491296976', 'local_bfl_moscow'),
            'UF_CREDIT_HISTORY' => $enum('UF_CREDIT_HISTORY', 'local_overdue'),
            'UF_CRM_1452153725' => $input['UF_CRM_1452153725'], 'UF_CRM_1452153749' => $input['UF_CRM_1452153749'],
            'UF_LOCAL_NATIVE_PAYLOAD' => $hash];
        if (array_key_exists('UF_TOTAL_DEBT_OTHER', $input)) {
            if (!is_numeric($input['UF_TOTAL_DEBT_OTHER']) || (float)$input['UF_TOTAL_DEBT_OTHER'] !== 0.0) {
                throw new InvalidArgumentException('Unsupported other debt fixture');
            }
            $fields['UF_TOTAL_DEBT_OTHER'] = (float)$input['UF_TOTAL_DEBT_OTHER'];
        }
        $fields['BIRTHDATE'] = $birth->format('d.m.Y');
        $native = new CCrmLead(false);
        $id = $native->Add($fields, true, ['CURRENT_USER' => native_actor_id()]);
        if (!$id) {
            file_put_contents('/tmp/native-add-private-error.log', (string)$native->LAST_ERROR);
            throw new RuntimeException('Original CCrmLead Add returned failure');
        }
        Bitrix\Crm\EntityAddress::register(CCrmOwnerType::Lead, (int)$id, Bitrix\Crm\EntityAddressType::Primary,
            ['ADDRESS_1' => 'Local synthetic address', 'PROVINCE' => $input['ADDRESS_PROVINCE'], 'COUNTRY' => 'Россия', 'COUNTRY_CODE' => 'RU']);
        $read = native_read((int)$id);
        native_assert_saved($read, $input, $metadata, $phone, $hash);
        return ['leadID' => (string)$id, 'created' => true];
    } finally {
        $connection->unlock($lock);
    }
}

function native_update(array $input): bool
{
    if (is_bool($input['id'] ?? null)) {
        throw new InvalidArgumentException('Invalid native update identity');
    }
    $id = filter_var($input['id'] ?? null, FILTER_VALIDATE_INT);
    if (!$id || $id < 1 || !is_array($input['fields'] ?? null)) {
        throw new InvalidArgumentException('Invalid native update envelope');
    }
    native_read((int)$id);
    $fields = $input['fields'];
    if (!$fields || array_diff(array_keys($fields), ['UF_SHIPPED_PARTNER', 'UF_SHIPPED_DATETIME', 'UF_PARTNER_NAME'])) {
        throw new InvalidArgumentException('Unsupported native update fields');
    }
    $native = new CCrmLead(false);
    if (!$native->Update((int)$id, $fields, true, true, ['CURRENT_USER' => native_actor_id()])) {
        file_put_contents('/tmp/native-update-private-error.log', (string)$native->LAST_ERROR);
        throw new RuntimeException('Original CCrmLead Update returned failure');
    }
    return true;
}
