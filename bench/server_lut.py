import numpy as np
_ANCH = np.array([[0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122], [229, 80, 100],
                  [251, 135, 97], [254, 194, 135], [252, 253, 191]], float)
LUT = np.stack([np.interp(np.linspace(0, 1, 256), np.linspace(0, 1, len(_ANCH)), _ANCH[:, c]) for c in range(3)], 1).astype(np.uint8)

