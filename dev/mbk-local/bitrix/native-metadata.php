<?php
// Metadata only: all user fields and enum values use original native APIs.
function native_definitions(): array
{
    return [
        'UF_CRM_1452152621' => ['string', false],
        'UF_SHIPPED_DATETIME' => ['datetime', false],
        'UF_SHIPPED_PARTNER' => ['boolean', false],
        'UF_PARTNER_NAME' => ['enumeration', true, ['microservice_bankruptcy' => 'Local BFL partner stream']],
        'UF_DEBT_SUMM' => ['double', false],
        'UF_TOTAL_DEBT_OTHER' => ['double', false],
        'UF_GENERAL_MONTHLY_PAYMENTS' => ['double', false],
        'UF_CRM_1452154548' => ['enumeration', false, ['local_bfl' => 'Local BFL']],
        'UF_SITE_AGREE_PARTNERS' => ['enumeration', false, ['local_yes' => 'Local consent yes']],
        'UF_CRM_1491296976' => ['enumeration', false, ['local_bfl_moscow' => 'Local Moscow BFL', 'local_bfl_regions' => 'Local regions BFL', 'local_no_category' => 'Local no category']],
        'UF_CREDIT_HISTORY' => ['enumeration', false, ['local_overdue' => 'Local overdue']],
        'UF_CRM_1452153725' => ['string', false],
        'UF_CRM_1452153749' => ['string', false],
        'UF_LOCAL_NATIVE_PAYLOAD' => ['string', false],
    ];
}

function native_metadata(bool $create): array
{
    $output = [];
    foreach (native_definitions() as $name => $definition) {
        $field = CUserTypeEntity::GetList([], ['ENTITY_ID' => 'CRM_LEAD', 'FIELD_NAME' => $name])->Fetch();
        if (!$field && $create) {
            $id = (new CUserTypeEntity())->Add(['ENTITY_ID' => 'CRM_LEAD', 'FIELD_NAME' => $name,
                'USER_TYPE_ID' => $definition[0], 'MULTIPLE' => $definition[1] ? 'Y' : 'N',
                'MANDATORY' => 'N', 'XML_ID' => 'synthetic_native_fixture_' . $name,
                'EDIT_FORM_LABEL' => ['ru' => $name], 'LIST_COLUMN_LABEL' => ['ru' => $name],
                'SETTINGS' => $definition[0] === 'string' ? ['MAX_LENGTH' => 255] : []]);
            if (!$id) {
                throw new RuntimeException('Native field metadata creation failed: ' . $name);
            }
            $field = CUserTypeEntity::GetList([], ['ID' => $id])->Fetch();
        }
        if (!$field || $field['USER_TYPE_ID'] !== $definition[0] || ($field['MULTIPLE'] === 'Y') !== $definition[1]) {
            throw new RuntimeException('Native user field metadata does not match fixture: ' . $name);
        }
        $enums = [];
        if (isset($definition[2])) {
            $values = [];
            $cursor = CUserFieldEnum::GetList([], ['USER_FIELD_ID' => $field['ID']]);
            while ($value = $cursor->Fetch()) {
                $enums[$value['XML_ID']] = (int)$value['ID'];
                $values[$value['ID']] = array_intersect_key($value, array_flip(['VALUE', 'XML_ID', 'SORT', 'DEF']));
            }
            $added = 0;
            foreach ($definition[2] as $xml => $label) {
                if (!isset($enums[$xml]) && $create) {
                    $values['n' . $added++] = ['VALUE' => $label, 'XML_ID' => $xml, 'SORT' => 100, 'DEF' => 'N'];
                }
            }
            if ($added && !(new CUserFieldEnum())->SetEnumValues((int)$field['ID'], $values)) {
                throw new RuntimeException('Native enum metadata creation failed');
            }
            $cursor = CUserFieldEnum::GetList([], ['USER_FIELD_ID' => $field['ID']]);
            while ($value = $cursor->Fetch()) {
                $enums[$value['XML_ID']] = (int)$value['ID'];
            }
            foreach ($definition[2] as $xml => $label) {
                if (!isset($enums[$xml])) {
                    throw new RuntimeException('Native enum fixture is missing');
                }
            }
        }
        $output[$name] = ['field_id' => (int)$field['ID'], 'type' => $field['USER_TYPE_ID'], 'multiple' => $field['MULTIPLE'] === 'Y', 'enums' => $enums];
    }
    if ($create && !CCrmStatus::GetList([], ['ENTITY_ID' => 'STATUS', 'STATUS_ID' => 'RESTORED'])->Fetch()) {
        $status = new CCrmStatus('STATUS');
        if (!$status->Add(['STATUS_ID' => 'RESTORED', 'NAME' => 'Local restored fixture', 'SORT' => 15, 'SYSTEM' => 'N'])) {
            file_put_contents('/tmp/native-metadata-private-error.log', (string)$status->GetLastError());
            throw new RuntimeException('Native RESTORED status metadata creation failed');
        }
    }
    return $output;
}
