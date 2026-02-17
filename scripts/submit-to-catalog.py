#!/usr/bin/env python3
"""
Submit data catalog to central registry.

This script transforms a dataCatalog entry from a source repository into a Dataset
entry suitable for submission to the central catalog, handling namespace prefix
management and validation.
"""

import argparse
import yaml
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple, Any


def load_yaml(filepath: str) -> dict:
    """Load YAML file safely."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def save_yaml(filepath: str, data: dict) -> None:
    """Save data to YAML file with proper formatting."""
    with open(filepath, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def extract_prefixes_from_yaml(data: dict) -> Dict[str, str]:
    """
    Extract all namespace prefixes used in the YAML data.
    Returns a dict of {prefix: namespace_uri}
    """
    prefixes = {}
    
    def find_prefix_in_string(value: str) -> List[Tuple[str, str]]:
        """Find CURIEs in a string (format: prefix:localname)."""
        found = []
        if isinstance(value, str):
            # Match CURIE patterns like "ex:dataset" or "schema:Thing"
            curie_pattern = r'\b([a-zA-Z_][a-zA-Z0-9_-]*):([a-zA-Z_][a-zA-Z0-9_-]*)\b'
            matches = re.findall(curie_pattern, value)
            for prefix, local_name in matches:
                found.append(prefix)
        return found
    
    def scan_value(value):
        """Recursively scan a value for prefixes."""
        if isinstance(value, dict):
            for k, v in value.items():
                scan_value(v)
        elif isinstance(value, list):
            for item in value:
                scan_value(item)
        elif isinstance(value, str):
            prefixes_found = find_prefix_in_string(value)
            for prefix in prefixes_found:
                prefixes[prefix] = None  # We'll fill in the URI later
    
    scan_value(data)
    return prefixes


def extract_prefixes_from_prefix_yaml(filepath: str) -> Dict[str, str]:
    """Load prefix definitions from a prefix.yaml file."""
    data = load_yaml(filepath)
    
    # Handle different YAML structures
    if isinstance(data, dict):
        if 'prefixes' in data:
            return data['prefixes']
        return data
    return {}


def transform_datacatalog_to_dataset(datacatalog: dict, repo_name: str, repo_url: str) -> dict:
    """
    Transform a dataCatalog entry into a Dataset entry.
    
    The dataCatalog becomes a Dataset. The source repository is added as a distribution.
    """
    dataset = {
        'identifier': datacatalog.get('identifier'),
        'title': datacatalog.get('title', 'Untitled Dataset'),
        'description': datacatalog.get('description', f'Dataset submitted from {repo_name}'),
    }
    
    # Copy over other fields from dataCatalog
    optional_fields = [
        'publisher', 'contactPoint', 'license', 'theme', 'temporal',
        'wasDerivedFrom', 'hasPolicy', 'issued', 'modified', 'version', 'status',
        'distribution'
    ]
    
    for field in optional_fields:
        if field in datacatalog and datacatalog[field] is not None:
            dataset[field] = datacatalog[field]
    
    # Add source repository as a distribution (not wasDerivedFrom)
    if 'distribution' not in dataset:
        dataset['distribution'] = []
    
    # Create distribution entry for the source repository
    source_distribution = {
        'identifier': f'{repo_name.replace("/", "-")}-source',
        'title': f'Source repository: {repo_name}',
        'description': f'This dataset was automatically submitted from {repo_url}',
        'accessURL': repo_url
    }
    
    if isinstance(dataset['distribution'], list):
        dataset['distribution'].append(source_distribution)
    
    return dataset


def merge_prefixes(source_prefixes: Dict[str, str], target_prefixes: Dict[str, str]) -> Tuple[Dict[str, str], List[str]]:
    """
    Merge source prefixes into target prefixes.
    Returns (merged_prefixes, list_of_new_prefixes_added)
    """
    merged = dict(target_prefixes)
    new_prefixes = []
    
    for prefix, uri in source_prefixes.items():
        if prefix not in merged:
            if uri:
                merged[prefix] = uri
                new_prefixes.append(prefix)
        elif uri and merged[prefix] != uri:
            # Conflict detected - same prefix, different URI
            print(f"WARNING: Prefix conflict for '{prefix}':", file=sys.stderr)
            print(f"  Target: {merged[prefix]}", file=sys.stderr)
            print(f"  Source: {uri}", file=sys.stderr)
    
    return merged, new_prefixes


def append_dataset_to_catalog(target_catalog: dict, new_dataset: dict) -> dict:
    """Append a new dataset to the target catalog's datasets list."""
    if 'datasets' not in target_catalog:
        target_catalog['datasets'] = []
    
    # Check if dataset with same identifier already exists
    existing_ids = {ds.get('identifier') for ds in target_catalog['datasets'] if isinstance(ds, dict)}
    if new_dataset.get('identifier') in existing_ids:
        print(f"WARNING: Dataset with identifier '{new_dataset.get('identifier')}' already exists in catalog", file=sys.stderr)
        # Generate a new unique identifier
        base_id = new_dataset['identifier']
        counter = 1
        while f"{base_id}-{counter}" in existing_ids:
            counter += 1
        new_dataset['identifier'] = f"{base_id}-{counter}"
        print(f"  Renamed to: {new_dataset['identifier']}", file=sys.stderr)
    
    target_catalog['datasets'].append(new_dataset)
    return target_catalog


def append_dataset_to_catalog_with_formatting(target_path: str, new_dataset: dict) -> str:
    """Append a new dataset to the target catalog by inserting it at the correct position in the YAML file."""
    with open(target_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    data = yaml.safe_load(content)
    
    if 'datasets' not in data:
        data['datasets'] = []
    
    existing_ids = {ds.get('identifier') for ds in data['datasets'] if isinstance(ds, dict)}
    
    dataset_id = new_dataset.get('identifier')
    if dataset_id in existing_ids:
        print(f"WARNING: Dataset with identifier '{dataset_id}' already exists in catalog", file=sys.stderr)
        base_id = dataset_id
        counter = 1
        while f"{base_id}-{counter}" in existing_ids:
            counter += 1
        new_dataset['identifier'] = f"{base_id}-{counter}"
        print(f"  Renamed to: {new_dataset['identifier']}", file=sys.stderr)
    
    new_dataset_yaml = yaml.dump(new_dataset, default_flow_style=False, allow_unicode=True, sort_keys=False)
    indented_lines = []
    for i, line in enumerate(new_dataset_yaml.strip().split('\n')):
        if line.strip():
            if i == 0:
                # First line: add list marker prefix
                indented_lines.append('  - ' + line)
            else:
                # Other lines: preserve relative indentation and add 4 spaces total indent
                # yaml.dump uses 2-space indents for nested content
                current_indent = len(line) - len(line.lstrip())
                target_indent = current_indent + 4
                indented_lines.append(' ' * target_indent + line.lstrip())
    new_dataset_block = '\n'.join(indented_lines)
    
    lines = content.split('\n')
    new_lines = []
    in_datasets = False
    datasets_indent = 0
    inserted = False
    
    for i, line in enumerate(lines):
        if line.strip().startswith('datasets:') and not in_datasets:
            in_datasets = True
            new_lines.append(line)
            if i + 1 < len(lines) and lines[i + 1].strip().startswith('-'):
                datasets_indent = len(lines[i + 1]) - len(lines[i + 1].lstrip())
            continue
        elif in_datasets:
            if line.strip() and not line.startswith(' ' * (datasets_indent - 1)) and not line.strip().startswith('#'):
                if not inserted:
                    new_lines.append(new_dataset_block)
                    inserted = True
                in_datasets = False
            elif line.strip().startswith(('concepts:', 'series:', 'metrics:', 'qualityMeasurements:', 'policies:', 'dataCatalog:', 'dataServices:')):
                if not inserted:
                    new_lines.append(new_dataset_block)
                    inserted = True
                in_datasets = False
                new_lines.append(line)
                continue
        
        new_lines.append(line)
    
    if not inserted:
        new_lines.append(new_dataset_block)
    
    new_content = '\n'.join(new_lines)
    
    with open(target_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    return new_dataset.get('identifier', '')


def generate_default_prefixes() -> Dict[str, str]:
    """Generate default/common namespace prefixes."""
    return {
        'linkml': 'https://w3id.org/linkml/',
        'sdcdc': 'https://www.uuidea.eu/profiles/data-catalog/',
        'dcat': 'http://www.w3.org/ns/dcat#',
        'odrl': 'http://www.w3.org/ns/odrl/2/',
        'prov': 'http://www.w3.org/ns/prov#',
        'xsd': 'http://www.w3.org/2001/XMLSchema#',
        'skos': 'http://www.w3.org/2004/02/skos/core#',
        'dcterms': 'http://purl.org/dc/terms/',
        'foaf': 'http://xmlns.com/foaf/0.1/',
        'vcard': 'http://www.w3.org/2006/vcard/ns#',
        'time': 'http://www.w3.org/2006/time#',
        'dqv': 'http://www.w3.org/ns/dqv#',
        'adms': 'http://www.w3.org/ns/adms#',
        'schema': 'http://schema.org/',
    }


def main():
    parser = argparse.ArgumentParser(description='Submit data catalog to central registry')
    parser.add_argument('--source', required=True, help='Path to source data-catalog.yaml')
    parser.add_argument('--target', required=True, help='Path to target data-catalog.yaml')
    parser.add_argument('--target-prefixes', required=True, help='Path to target prefix.yaml')
    parser.add_argument('--repo-name', required=True, help='Source repository name (owner/repo)')
    parser.add_argument('--repo-url', required=True, help='Source repository URL')
    
    args = parser.parse_args()
    
    # Load source data catalog
    print(f"Loading source catalog from {args.source}")
    source_data = load_yaml(args.source)
    
    if 'dataCatalog' not in source_data:
        print("ERROR: No dataCatalog entry found in source file", file=sys.stderr)
        sys.exit(1)
    
    datacatalog = source_data['dataCatalog']
    print(f"Found dataCatalog: {datacatalog.get('identifier', 'unknown')}")
    
    # Extract prefixes used in source
    source_prefixes_used = extract_prefixes_from_yaml(source_data)
    print(f"Found {len(source_prefixes_used)} prefixes used in source: {', '.join(source_prefixes_used.keys())}")
    
    # Load target prefix.yaml if it exists
    target_prefixes = {}
    if Path(args.target_prefixes).exists():
        print(f"Loading target prefixes from {args.target_prefixes}")
        target_prefixes = extract_prefixes_from_prefix_yaml(args.target_prefixes)
    else:
        print(f"Target prefix.yaml not found at {args.target_prefixes}, creating new one")
        target_prefixes = generate_default_prefixes()
    
    # Build complete source prefixes with URIs
    # First, get default prefixes
    default_prefixes = generate_default_prefixes()
    
    # Then, try to extract from source's data-catalog.yaml (look for prefixes section)
    source_defined_prefixes = {}
    if 'prefixes' in source_data:
        source_defined_prefixes = source_data['prefixes']
    
    # Also load source's prefix.yaml file if it exists
    source_prefix_file = Path(args.source).parent / 'prefix.yaml'
    if source_prefix_file.exists():
        with open(source_prefix_file, 'r') as f:
            prefix_file_data = yaml.safe_load(f)
            if prefix_file_data and 'prefixes' in prefix_file_data:
                for prefix, uri in prefix_file_data['prefixes'].items():
                    if prefix not in source_defined_prefixes:
                        source_defined_prefixes[prefix] = uri
    
    # Merge to get full source prefixes - include all defined prefixes from source
    complete_source_prefixes = {}
    for prefix in source_defined_prefixes:
        complete_source_prefixes[prefix] = source_defined_prefixes[prefix]
    
    # Also add prefixes that are used but not yet in complete_source_prefixes
    for prefix in source_prefixes_used:
        if prefix not in complete_source_prefixes:
            if prefix in default_prefixes:
                complete_source_prefixes[prefix] = default_prefixes[prefix]
            else:
                complete_source_prefixes[prefix] = None
                print(f"WARNING: Unknown prefix '{prefix}' - no URI definition found", file=sys.stderr)
    
    # Merge prefixes
    merged_prefixes, new_prefixes = merge_prefixes(complete_source_prefixes, target_prefixes)
    
    if new_prefixes:
        print(f"Adding {len(new_prefixes)} new prefixes: {', '.join(new_prefixes)}")
        
        # Write prefix changes file for PR description
        changes_md = []
        for prefix in new_prefixes:
            uri = merged_prefixes[prefix]
            changes_md.append(f"- `{prefix}`: `{uri}`")
        
        # Save to file for the workflow to use
        with open('target-repo/prefix_changes.md', 'w') as f:
            f.write('\n'.join(changes_md))
    else:
        print("No new prefixes to add")
    
    # Update target prefix.yaml
    prefix_data = {'prefixes': merged_prefixes}
    save_yaml(args.target_prefixes, prefix_data)
    print(f"Updated {args.target_prefixes}")
    
    # Load target catalog to check existing datasets
    print(f"Loading target catalog from {args.target}")
    target_catalog = load_yaml(args.target)
    
    # Transform dataCatalog to Dataset
    print("Transforming dataCatalog to Dataset...")
    new_dataset = transform_datacatalog_to_dataset(
        datacatalog, 
        args.repo_name, 
        args.repo_url
    )
    
    print(f"Created dataset: {new_dataset['identifier']}")
    
    # Append to target catalog while preserving original formatting
    dataset_id = append_dataset_to_catalog_with_formatting(args.target, new_dataset)
    print(f"Updated {args.target}")
    
    print("\nSubmission preparation complete!")
    print(f"  - Dataset added: {new_dataset['identifier']}")
    print(f"  - New prefixes added: {len(new_prefixes)}")
    if new_prefixes:
        print(f"  - Prefixes: {', '.join(new_prefixes)}")


if __name__ == '__main__':
    main()
