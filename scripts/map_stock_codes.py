import argparse
import os
import sys

# Add the project root to the Python path to allow importing from data_source
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from data_source.hikyuu_data_source import HikyuuDataSource


def transform_code(qlib_code):
    """
    Transforms a qlib-style stock code (e.g., '159001.SZ') to hikyuu-style (e.g., 'SZ159001').
    """
    parts = qlib_code.strip().split('.')
    if len(parts) != 2:
        return None
    code, market = parts
    return f"{market.upper()}{code}"


def main():
    parser = argparse.ArgumentParser(description="Map stock codes to display names using Hikyuu.")
    parser.add_argument("input_file", help="Path to the input text file with stock codes.")
    parser.add_argument("output_file", help="Path to the output text file.")
    args = parser.parse_args()

    if not os.path.exists(args.input_file):
        print(f"Error: Input file not found at {args.input_file}")
        return

    try:
        data_source = HikyuuDataSource()
    except Exception as e:
        print(f"Error initializing HikyuuDataSource: {e}")
        return

    print(f"Reading from {args.input_file} and writing to {args.output_file}...")

    with open(args.input_file, 'r') as infile, open(args.output_file, 'w',encoding='utf-8') as outfile:
        for line in infile:
            line = line.strip()
            if not line:
                continue

            parts = line.split()
            if not parts:
                continue

            original_code = parts[0]
            hikyuu_code = transform_code(original_code)

            if not hikyuu_code:
                print(f"Warning: Could not parse code from line: '{line}'")
                continue

            try:
                info = data_source.get_security_info(hikyuu_code)
                if info and info.display_name:
                    outfile.write(f"{original_code},{info.display_name}\n")
                    print(f"Mapped {original_code} -> {info.display_name}")
                else:
                    outfile.write(f"{original_code},NOT_FOUND")
                    print(f"Warning: Could not find info for {hikyuu_code} ({original_code})")
            except Exception as e:
                print(f"Error processing code {hikyuu_code}: {e}")
                outfile.write(f"{original_code},ERROR")

    print("Processing complete.")


if __name__ == "__main__":
    main()
