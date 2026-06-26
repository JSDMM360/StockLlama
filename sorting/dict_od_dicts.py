import os
import json

def load_reviews_from_directory(directory, start_index, container):
    files = os.listdir(directory)

    for file in files:
        if not file.endswith(".json"):
            continue

        file_path = os.path.join(directory, file)

        try:
            with open(file_path, "r") as f:
                data = json.load(f)

            container[str(start_index)] = data
            start_index += 1

        except Exception as e:
            print(f"Error reading {file}: {e}")

    return start_index


def create_dict_of_dicts(output_path):
    directories = [
        "assets/sorted/json/reviews/user_reviews/",
        "assets/sorted/json/reviews/sanitized_reviews/"
    ]

    dict_of_dicts = {}
    index = 0

    for directory in directories:
        if os.path.exists(directory):
            index = load_reviews_from_directory(directory, index, dict_of_dicts)
        else:
            print(f"Directory not found: {directory}")

    with open(output_path, "w") as out_file:
        json.dump(dict_of_dicts, out_file, indent=4)

    print(f"dictOfDicts.json created with {len(dict_of_dicts)} reviews.")


create_dict_of_dicts("dictOfDicts.json")