"""FHIR R4 resources (NDJSON lines) for each module, aligned with ABDM/NRCeS usage where relevant."""

BASE = "https://workframe.health/fhir"
SYS = {"ICD-10": "http://hl7.org/fhir/sid/icd-10", "LOINC": "http://loinc.org", "ATC": "http://www.whocc.no/atc",
       "UCUM": "http://unitsofmeasure.org", "DICOM": "http://dicom.nema.org/resources/ontology/DCM",
       "WF-PROC": f"{BASE}/CodeSystem/procedure", "SPECIALTY": f"{BASE}/CodeSystem/specialty",
       "LOCAL": f"{BASE}/CodeSystem/local-lab"}
ENC_CLASS = {"Inpatient": ("IMP", "inpatient encounter"), "Outpatient": ("AMB", "ambulatory"),
             "Emergency": ("EMER", "emergency"), "Day care": ("SS", "short stay"), "Virtual": ("VR", "virtual"),
             "Other": ("AMB", "ambulatory")}


def ext(name, **v):
    return {"url": f"{BASE}/StructureDefinition/{name}", **v}


def patient(r):
    e = [ext("age-band", valueString=r["age_band"])]
    if r.get("region"):
        e.append(ext("region", valueString=r["region"]))
    if r.get("payer_category"):
        e.append(ext("payer-category", valueString=r["payer_category"]))
    g = {"Male": "male", "Female": "female", "Other": "other"}.get(r["sex"], "unknown")
    return {"resourceType": "Patient", "id": r["anon_patient_id"], "gender": g, "extension": e}


def encounter(r):
    c, d = ENC_CLASS.get(r["encounter_type"], ("AMB", "ambulatory"))
    res = {"resourceType": "Encounter", "id": r["anon_encounter_id"], "status": "finished",
           "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": c, "display": d},
           "subject": {"reference": f"Patient/{r['anon_patient_id']}"},
           "period": {"start": r["encounter_date_month"]}}
    if r.get("specialty_code"):
        res["serviceType"] = {"coding": [{"system": SYS["SPECIALTY"], "code": r["specialty_code"], "display": r["specialty"]}]}
    e = []
    if r.get("length_of_stay_days") not in (None, ""):
        e.append(ext("length-of-stay-days", valueInteger=int(r["length_of_stay_days"])))
    if r.get("ward_type"):
        e.append(ext("ward-type", valueString=r["ward_type"]))
    if e:
        res["extension"] = e
    return res


def condition(r, i):
    res = {"resourceType": "Condition", "id": f"{r['anon_encounter_id']}-dx{i}",
           "code": {"coding": [{"system": SYS["ICD-10"], "code": r["diagnosis_code"], "display": r["diagnosis_display"]}]},
           "subject": {"reference": f"Patient/{r['anon_patient_id']}"},
           "encounter": {"reference": f"Encounter/{r['anon_encounter_id']}"},
           "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category", "code": "encounter-diagnosis"}]}]}
    if r.get("years_since_diagnosis") not in (None, ""):
        res["extension"] = [ext("years-since-diagnosis", valueInteger=int(r["years_since_diagnosis"]))]
    return res


def procedure(r, i):
    res = {"resourceType": "Procedure", "id": f"{r['anon_encounter_id']}-px{i}", "status": "completed",
           "code": {"coding": [{"system": SYS["WF-PROC"], "code": r["procedure_code"], "display": r["procedure_display"]}]},
           "subject": {"reference": f"Patient/{r['anon_patient_id']}"},
           "encounter": {"reference": f"Encounter/{r['anon_encounter_id']}"}}
    if r.get("procedure_month"):
        res["performedDateTime"] = r["procedure_month"]
    return res


def medication(r, i):
    rt = "MedicationRequest" if r["medication_context"] == "Discharge prescription" else "MedicationStatement"
    res = {"resourceType": rt, "id": f"{r['anon_encounter_id']}-rx{i}",
           "status": "active" if rt == "MedicationRequest" else "completed",
           "medicationCodeableConcept": {"coding": [{"system": SYS["ATC"], "code": r["medication_code"]}]},
           "subject": {"reference": f"Patient/{r['anon_patient_id']}"}}
    if rt == "MedicationRequest":
        res["intent"] = "order"
        res["encounter"] = {"reference": f"Encounter/{r['anon_encounter_id']}"}
    else:
        res["context"] = {"reference": f"Encounter/{r['anon_encounter_id']}"}
    dose = " ".join(x for x in (r.get("strength"), r.get("dose_pattern"), r.get("frequency"),
                                f"{r['duration_days']} days" if r.get("duration_days") else "") if x)
    if dose or r.get("route"):
        key = "dosageInstruction" if rt == "MedicationRequest" else "dosage"
        res[key] = [{"text": dose, **({"route": {"text": r["route"]}} if r.get("route") else {})}]
    return res


def observation(r, i, category):
    sysurl = SYS.get(r["code_system"], SYS["LOCAL"])
    res = {"resourceType": "Observation", "id": f"{r['anon_encounter_id']}-{category[:3]}{i}",
           "status": {"Final": "final", "Amended": "amended", "Preliminary": "preliminary", "Cancelled": "cancelled"}.get(r.get("result_status", "Final"), "final"),
           "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": category}]}],
           "code": {"coding": [{"system": sysurl, "code": r["observation_code"]}]},
           "subject": {"reference": f"Patient/{r['anon_patient_id']}"},
           "encounter": {"reference": f"Encounter/{r['anon_encounter_id']}"}}
    if r.get("observation_month"):
        res["effectiveDateTime"] = r["observation_month"]
    v = r.get("observation_value")
    try:
        res["valueQuantity"] = {"value": float(v), "unit": r.get("observation_units", ""), "system": SYS["UCUM"],
                                "code": r.get("observation_units", "")}
    except (TypeError, ValueError):
        res["valueString"] = str(v)
    return res


def document(r):
    return {"resourceType": "DocumentReference", "id": r["anon_document_id"], "status": "current",
            "type": {"coding": [{"system": SYS["LOINC"], "code": r["document_type_code"], "display": r["document_type"]}]},
            "subject": {"reference": f"Patient/{r['anon_patient_id']}"},
            "date": r["document_month"],
            "context": {"encounter": [{"reference": f"Encounter/{r['anon_encounter_id']}"}]},
            "content": [{"attachment": {"contentType": "application/pdf", "url": r["file"]}, "format": {"code": "PDF/A"}}]}


def imaging_study(r, series):
    res = {"resourceType": "ImagingStudy", "id": r["anon_imaging_study_id"], "status": "available",
           "subject": {"reference": f"Patient/{r['anon_patient_id']}"},
           "started": r["study_date_month"],
           "modality": [{"system": SYS["DICOM"], "code": r["modality"]}],
           "numberOfSeries": int(r["number_of_series"]), "numberOfInstances": int(r["number_of_instances"]),
           "extension": [ext("contrast-used", valueString=r["contrast_used"])]}
    if series:
        res["series"] = [{"uid": s["uid"], "number": s["number"], "modality": {"system": SYS["DICOM"], "code": r["modality"]},
                          "numberOfInstances": s["count"], **({"bodySite": {"display": r["body_region"]}} if r.get("body_region") else {})}
                         for s in series]
    elif r.get("body_region"):
        res["extension"].append(ext("body-region", valueString=r["body_region"]))
    if not r.get("dicom_folder"):
        res["extension"].append(ext("images-supplied", valueString="No (report only)"))
    if r.get("anon_encounter_id"):
        res["encounter"] = {"reference": f"Encounter/{r['anon_encounter_id']}"}
    return res


def diagnostic_report(r):
    return {"resourceType": "DiagnosticReport", "id": r["anon_imaging_report_id"],
            "status": r["report_status"].lower(),
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "RAD"}]}],
            "code": {"coding": [{"system": SYS["LOINC"], "code": "18748-4", "display": "Diagnostic imaging study"}]},
            "subject": {"reference": f"Patient/{r['anon_patient_id']}"},
            "imagingStudy": [{"reference": f"ImagingStudy/{r['anon_imaging_study_id']}"}],
            "conclusion": r["report_text"]}
