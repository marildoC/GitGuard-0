import pickle
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

# Carica la gallery
with open("data/gait_gallery.enc", "rb") as f:
    gallery = pickle.load(f)

identities = gallery['identities']

# Estrai gli embeddings EMA
names = []
embeddings = []
for name, data in identities.items():
    if hasattr(data, 'ema_embedding'):
        names.append(name)
        embeddings.append(data.ema_embedding)

embeddings = np.vstack(embeddings)
print(f"[INFO] Shape embeddings: {embeddings.shape}")

# Calcola la cosine similarity
sim_matrix = cosine_similarity(embeddings)

# Mostra le coppie con similarità maggiore di una soglia (es. 0.95)
threshold = 0.80
print(f"\n[INFO] Coppie con similarità > {threshold}:")
for i in range(len(names)):
    for j in range(i+1, len(names)):
        sim = sim_matrix[i, j]
        if sim > threshold:
            print(f"{names[i]} <> {names[j]} : similarity = {sim:.4f}")
