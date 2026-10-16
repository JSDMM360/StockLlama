import random

ingredients = [
    ("extra virgin olive oil", "ml"), ("unsalted butter", "grams"), 
    ("minced garlic", "grams"), ("kosher salt", "grams"), 
    ("black pepper", "grams"), ("all-purpose flour", "grams"), 
    ("granulated sugar", "grams"), ("whole milk", "ml"), 
    ("heavy cream", "ml"), ("chopped yellow onion", "grams"), 
    ("diced carrots", "grams"), ("celery stalks", "grams"), 
    ("ground cumin", "grams"), ("ground turmeric", "grams"), 
    ("paprika", "grams"), ("fresh rosemary", "sprigs"), 
    ("dried oregano", "grams"), ("balsamic vinegar", "ml"), 
    ("honey", "ml"), ("diced tomatoes", "grams")
]

adjectives = ["artisanal", "traditional", "aromatic", "hearty", "flavorful", "savory", "rich", "delicate", "zesty", "robust"]
dishes = ["stew", "sauce", "soup", "marinade", "bake", "roast", "casserole", "curry", "broth", "dressing"]
actions = ["whisking", "stirring", "simmering", "folding", "blending", "kneading", "sautéing", "combining"]
containers = ["stainless steel bowl", "heavy cast-iron skillet", "large ceramic pot", "glass prep bowl", "deep Dutch oven"]

sentences = []

for i in range(1, 1001):
    adj = random.choice(adjectives)
    dish = random.choice(dishes)
    action = random.choice(actions)
    container = random.choice(containers)
    
    # Pick 6 to 8 random ingredients with randomized quantities
    selected_ing = random.sample(ingredients, random.randint(6, 8))
    ing_strings = [f"{random.randint(5, 500)} {unit} of {name}" for name, unit in selected_ing]
    
    ing_list_str = ", ".join(ing_strings[:-1]) + f", and {ing_strings[-1]}"
    
    # Construct a sentence designed to hit 100-200 words
    sentence = (
        f"For batch number {i} of our {adj} kitchen preparation, the recipe requires meticulously gathering "
        f"{ing_list_str}, all of which must be measured precisely before being transferred directly into a clean {container}. "
        f"Once all components are safely inside the vessel, begin {action} everything continuously over low to medium heat for at least fifteen to twenty minutes, "
        f"ensuring that the temperature remains perfectly stable so that no scorching occurs along the bottom edges of the dish. "
        f"As the mixture cooks down, the natural aromas will develop significantly, signaling that the {dish} is reaching its optimal consistency and target viscosity. "
        f"At this stage, carefully monitor the pot while maintaining a soft, continuous stir, making sure that every single ingredient distributes evenly throughout the bath "
        f"before taking the pan off the burner, letting it rest uncovered at room temperature for ten minutes, and finally serving it alongside fresh crusty bread or storing it in airtight glass jars for future meal planning."
    )
    
    words = sentence.split()
    # Verification check to guarantee word count rule
    assert 100 <= len(words) <= 200, f"Word count out of range: {len(words)}"
    sentences.append(f"[{i}] {sentence}")

with open("kitchen_sentences.txt", "w", encoding="utf-8") as f:
    f.write("\n\n".join(sentences))

print("Successfully generated 1000 sentences in 'kitchen_sentences.txt'!")
